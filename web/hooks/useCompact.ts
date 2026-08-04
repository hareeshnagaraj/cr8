"use client";

import {useEffect, useState} from "react";

export function useCompact() {
  const [compact, setCompact] = useState(false);
  useEffect(() => {
    const measure = () => setCompact(window.innerWidth <= 768);
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  return compact;
}
