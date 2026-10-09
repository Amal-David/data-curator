export type Role = "source" | "target" | "holdout";
export type RecordItem = {
  id: string;
  text: string;
  kind: string;
  metadata?: Record<string, unknown>;
  dataset_id?: string;
  source_record_id?: string;
  quality_score?: number;
  relevance_score?: number;
  lineage?: Record<string, unknown>;
};
export type Dataset = {
  id: string;
  name: string;
  role: Role;
  sha256: string;
  created_at: string;
  record_count: number;
  records?: RecordItem[];
};
export type Recipe = {
  name: string;
  seed: number;
  clean: {
    min_chars: number;
    max_chars: number;
    dedup_threshold: number;
    holdout_ngram: number;
    holdout_overlap: number;
  };
  calibrate: { task: string; quality_min: number; relevance_min: number };
  create: {
    enabled: boolean;
    endpoint: string;
    model: string;
    max_records: number;
    temperature: number;
  };
  compose: { max_records: number; weights: Record<string, number> };
};
export type Decision = {
  record_id: string;
  dataset_id: string;
  stage: string;
  action: string;
  reason: string;
  details?: Record<string, unknown>;
};
export type Run = {
  id: string;
  status: "queued" | "running" | "completed" | "cancelled" | "failed";
  created_at: string;
  recipe: Recipe;
  dataset_ids: string[];
  progress?: {
    stage: string;
    completed: number;
    total: number;
    message: string;
  };
  summary?: Record<string, unknown>;
  manifest?: Record<string, unknown>;
  error?: string;
};
export type SavedRecipe = { id: string; name: string; recipe: Recipe };
