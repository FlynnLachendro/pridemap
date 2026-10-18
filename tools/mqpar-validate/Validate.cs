// Validates a generated mqpar.xml by parsing it with MaxQuant's OWN libraries, then exercising the
// accessors that abort a real run. Structural checks live in tests/test_mqpar_contract.py; this goes
// one level deeper, because MaxQuant's reader is the only thing that truly agrees with MaxQuant.
//
// Each check corresponds to a failure observed on PXD046787, with the job number it aborted at.
// Exit code 0 = all checks passed, 1 = at least one failed.
using System;
using System.Collections;
using System.Linq;
using System.Reflection;

internal static class Validate {
    private const BindingFlags Inst = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance;
    private const BindingFlags Stat = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Static;

    private static int _failures;
    private static string _mqDir = "/opt/maxquant";

    private static void Pass(string what) => Console.WriteLine("  PASS  " + what);

    private static void Fail(string what, string why) {
        _failures++;
        Console.WriteLine("  FAIL  " + what + "\n          " + why);
    }

    private static Assembly Load(string dll) => Assembly.LoadFrom(_mqDir + "/" + dll);

    private static int Main(string[] args) {
        if (args.Length < 1) {
            Console.Error.WriteLine("usage: Validate <mqpar.xml> [maxquant-dir]");
            return 2;
        }
        if (args.Length > 1) _mqDir = args[1];

        Assembly mqAsm, baseAsm;
        Type mqParamsType;
        object mqpar;
        try {
            mqAsm = Load("MaxQuantLibS.dll");
            baseAsm = Load("BaseLibS.dll");
            mqParamsType = mqAsm.GetTypes().First(t => t.Name == "MaxQuantParams");
        } catch (Exception ex) {
            Console.Error.WriteLine("cannot load MaxQuant assemblies from " + _mqDir + ": " + ex.Message);
            return 2;
        }

        Console.WriteLine("validating " + args[0]);

        // ---- the config parses at all
        try {
            var read = mqParamsType.GetMethod("Read", Stat, null, new[] { typeof(string) }, null);
            mqpar = read.Invoke(null, new object[] { args[0] });
            if (mqpar == null) { Fail("MaxQuantParams.Read", "returned null"); return Report(); }
            Pass("MaxQuantParams.Read parses the config");
        } catch (Exception ex) {
            Fail("MaxQuantParams.Read", Unwrap(ex));
            return Report();
        }

        // ---- job 8: the analyser lookup MS/MS preparation does
        try {
            var msms = (IDictionary) mqParamsType.GetField("msmsParams", Inst).GetValue(mqpar);
            if (msms == null) Fail("msmsParams", "is null; MS/MS preparation looks analysers up in it (job 8)");
            else if (!msms.Contains("FTMS"))
                Fail("msmsParams[\"FTMS\"]", "missing; an Orbitrap run throws KeyNotFoundException at job 8");
            else Pass("msmsParams has all " + msms.Count + " analysers, including FTMS (job 8)");
        } catch (Exception ex) { Fail("msmsParams", Unwrap(ex)); }

        // ---- job 48: present-but-empty referenceChannel makes this getter throw
        try {
            var prop = mqParamsType.GetProperty("ReferenceChannels", Inst);
            var channels = prop.GetValue(mqpar, null) as Array;
            Pass("ReferenceChannels resolves (" + (channels == null ? "null" : channels.Length.ToString())
                 + " outer) — job 48");
        } catch (Exception ex) {
            Fail("ReferenceChannels", Unwrap(ex)
                 + "  (referenceChannel must be ABSENT, not present-and-empty; aborts job 48)");
        }

        // ---- job 19: every modification name must resolve against MaxQuant's tables
        try {
            var tables = baseAsm.GetTypes().First(t => t.FullName == "BaseLibS.Mol.Tables");
            var toMods = tables.GetMethods(Stat).First(m => m.Name == "ToModifications");
            CheckMods(toMods, mqParamsType.GetField("restrictMods", Inst).GetValue(mqpar),
                      "restrictMods", "Tables.ToModifications throws on it while preparing the combined folder (job 19)");

            var groups = (Array) mqParamsType.GetField("groups", Inst).GetValue(mqpar);
            if (groups == null || groups.Length == 0) {
                Fail("parameterGroups", "no parameter group was parsed");
            } else {
                var group = groups.GetValue(0);
                var gType = group.GetType();
                foreach (var field in new[] { "variableModifications", "fixedModifications" })
                    CheckMods(toMods, gType.GetField(field, Inst).GetValue(group), field,
                              "a modification name does not exist in MaxQuant's tables");

                // ---- job 8: labelMods is indexed by the multiplicity counter, so it must match
                var labels = gType.GetField("labelMods", Inst).GetValue(group) as IList;
                var multiplicity = Convert.ToInt32(gType.GetProperty("Multiplicity", Inst).GetValue(group, null));
                if (labels == null)
                    Fail("labelMods", "is null; GetLmods() dereferences it at deisotoping (job 7)");
                else if (labels.Count != multiplicity)
                    Fail("labelMods", "has " + labels.Count + " entries but multiplicity is " + multiplicity
                         + "; the label list is indexed by that counter (job 8)");
                else
                    Pass("labelMods has " + labels.Count + " entr(y/ies), matching multiplicity (job 8)");
            }
        } catch (Exception ex) { Fail("modification resolution", Unwrap(ex)); }

        return Report();
    }

    private static void CheckMods(MethodInfo toMods, object value, string what, string why) {
        if (value == null) { Fail(what, "is null; " + why); return; }
        try {
            var resolved = toMods.Invoke(null, new[] { value }) as ICollection;
            Pass(what + " resolves to " + (resolved == null ? "null" : resolved.Count.ToString()) + " modification(s)");
        } catch (Exception ex) {
            Fail(what, Unwrap(ex) + "; " + why);
        }
    }

    private static string Unwrap(Exception ex) {
        var inner = ex.InnerException ?? ex;
        return inner.GetType().Name + ": " + inner.Message;
    }

    private static int Report() {
        Console.WriteLine();
        Console.WriteLine(_failures == 0 ? "OK: config satisfies MaxQuant's own accessors"
                                         : "FAILED: " + _failures + " check(s)");
        return _failures == 0 ? 0 : 1;
    }
}
