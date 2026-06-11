"use client";
/**
 * Session hook backed by /api/me with cookie auth.
 * - undefined while loading
 * - null when unauthenticated
 * - Me when authenticated
 */
import useSWR from "swr";
import { fetcher } from "./api";

export type Me = {
  id: string;
  email: string;
  display_name: string;
  tenant_id: string;
  role?: string;
  timezone?: string;
};

export function useSession() {
  const { data, error, isLoading, mutate } = useSWR<Me>("/api/me", fetcher, {
    shouldRetryOnError: false,
    revalidateOnFocus: false,
  });
  const status: "loading" | "authenticated" | "unauthenticated" =
    isLoading ? "loading" : data ? "authenticated" : "unauthenticated";
  return { user: data ?? null, status, error, refresh: mutate };
}
