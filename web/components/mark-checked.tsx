"use client";
import { useEffect } from "react";
import { markChecked } from "@/lib/progress-store";
export function MarkChecked() { useEffect(() => { markChecked(); }, []); return null; }
