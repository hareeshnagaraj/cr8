"use client";

import {useEffect, useState} from "react";

export type Member = {username: string; display: string};

type MemberLoad = {
  members: Member[];
  loading: boolean;
  error: string;
};

function memberList(value: unknown): Member[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Unexpected member response");
  }
  const members = (value as {members?: unknown}).members;
  if (!Array.isArray(members)) throw new Error("Unexpected member response");
  return members.map((value) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("Unexpected member response");
    }
    const {username, display} = value as Record<string, unknown>;
    if (typeof username !== "string" || typeof display !== "string") {
      throw new Error("Unexpected member response");
    }
    return {username, display};
  });
}

export function useMembers(): MemberLoad {
  const [load, setLoad] = useState<MemberLoad>({
    members: [],
    loading: true,
    error: "",
  });

  useEffect(() => {
    const controller = new AbortController();

    async function loadMembers() {
      try {
        const response = await fetch("/api/members", {
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Member request failed (${response.status})`);
        }
        const members = memberList(await response.json());
        if (!controller.signal.aborted) {
          setLoad({members, loading: false, error: ""});
        }
      } catch (problem) {
        if (controller.signal.aborted) return;
        setLoad({members: [], loading: false, error: "Could not load people."});
      }
    }

    void loadMembers();
    return () => controller.abort();
  }, []);

  return load;
}
