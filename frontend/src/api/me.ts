import { useQuery } from "@tanstack/react-query";

import { client, unwrap } from "./client";
import type { components } from "./schema";

export type Me = components["schemas"]["MeOut"];

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => unwrap(await client.GET("/api/v1/me")),
    retry: false,
    staleTime: 30_000,
  });
}
