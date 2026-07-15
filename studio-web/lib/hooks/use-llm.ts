"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/client";

export function useLLMConfig() {
  return useQuery({
    queryKey: ["llm-config"],
    queryFn: () => api.getLLMConfig(),
  });
}

export function useProviders() {
  return useQuery({
    queryKey: ["llm-providers"],
    queryFn: () => api.getProviders(),
  });
}

export function useUpdateLLMConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (config: api.LLMConfigRequest) => api.updateLLMConfig(config),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["llm-config"] });
      qc.invalidateQueries({ queryKey: ["settings"] });
    },
  });
}

export function useValidateProvider() {
  return useMutation({
    mutationFn: (data: { provider: string; api_key: string; base_url?: string | null }) =>
      api.validateProvider(data),
  });
}

// Discover the model ids a provider offers (for the chat model chooser).
// Cached per provider; failures/keyless providers just yield an empty list,
// so the caller falls back to free-text entry.
export function useProviderModels(
  provider: string | null,
  apiKey: string,
  baseUrl?: string | null
) {
  return useQuery({
    queryKey: ["provider-models", provider, apiKey, baseUrl],
    queryFn: () =>
      api.validateProvider({
        provider: provider as string,
        api_key: apiKey,
        base_url: baseUrl ?? null,
      }),
    enabled: !!provider,
    staleTime: 5 * 60_000,
    retry: false,
  });
}

// --- Multi-provider config hooks ---

export function useProviderConfigs() {
  return useQuery({
    queryKey: ["provider-configs"],
    queryFn: () => api.getProviderConfigs(),
  });
}

export function useUpsertProviderConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (config: api.ProviderConfigRequest) =>
      api.upsertProviderConfig(config),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["provider-configs"] });
      qc.invalidateQueries({ queryKey: ["llm-config"] });
    },
  });
}

export function useActivateProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (providerId: string) => api.activateProvider(providerId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["provider-configs"] });
      qc.invalidateQueries({ queryKey: ["llm-config"] });
    },
  });
}

export function useDeleteProviderConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (providerId: string) => api.deleteProviderConfig(providerId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["provider-configs"] });
    },
  });
}
