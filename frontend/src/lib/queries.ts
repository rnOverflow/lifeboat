import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Backup, type Fault, type GameDay, type Incident, type Plan, type Run, type Status } from "./api";

const FAST = 1500;

export const useStatus = () => useQuery({ queryKey: ["status"], queryFn: () => api<Status>("/status"), refetchInterval: FAST });
export const usePlan = () => useQuery({ queryKey: ["plan"], queryFn: () => api<Plan>("/plan"), refetchInterval: 3000 });
export const useRuns = () => useQuery({ queryKey: ["runs"], queryFn: () => api<Run[]>("/runs?limit=30"), refetchInterval: FAST });
export const useRun = (id: number | null) =>
  useQuery({ queryKey: ["run", id], queryFn: () => api<Run>(`/runs/${id}`), enabled: id !== null, refetchInterval: FAST });
export const useBackups = () => useQuery({ queryKey: ["backups"], queryFn: () => api<Backup[]>("/backups"), refetchInterval: 4000 });
export const useFaults = () => useQuery({ queryKey: ["faults"], queryFn: () => api<Fault[]>("/simulator/faults"), refetchInterval: FAST });
export const useIncidents = () => useQuery({ queryKey: ["incidents"], queryFn: () => api<Incident[]>("/incidents?limit=100"), refetchInterval: 3000 });
export const useGameDays = () => useQuery({ queryKey: ["gamedays"], queryFn: () => api<GameDay[]>("/gamedays"), refetchInterval: FAST });
export interface Sample { ts: string; healthy: boolean; latency_ms: number; replication_lag_s: number | null }
export const useHistory = (region: string | undefined, minutes = 10) =>
  useQuery({
    queryKey: ["history", region, minutes],
    queryFn: () => api<Sample[]>(`/regions/${region}/history?minutes=${minutes}`),
    enabled: !!region,
    refetchInterval: 3000,
  });

/** Mutation that invalidates every live query on success so the UI reflects the new state immediately. */
export function useAction<TVars = void, TOut = unknown>(fn: (v: TVars) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSettled: () => qc.invalidateQueries() });
}
