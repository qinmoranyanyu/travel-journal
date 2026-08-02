export type JobStatus = "queued" | "running" | "completed" | "partial" | "failed" | "interrupted";

export interface JobSnapshot {
  id: string;
  status: JobStatus;
  stage: string;
  progress: number;
  message: string;
  completed_items: number;
  total_items: number;
  created_at: string;
  updated_at: string;
  error?: string | null;
  output_url?: string | null;
  share_url?: string | null;
  zip_url?: string | null;
  export_urls: string[];
  can_stop_retries: boolean;
  retry_stop_requested: boolean;
  retry_round: number;
  failed_items: number;
}

export interface AlbumInput {
  title: string;
  location: string;
  companions: string;
  memory: string;
  target_count: number;
}

export interface JobUpload {
  id: string;
  original_name: string;
  order: number;
  modified_at?: string | null;
  preview_url: string;
}

export interface JobDetail {
  snapshot: JobSnapshot;
  album_input: AlbumInput;
  uploads: JobUpload[];
}

export interface AlbumSummary {
  id: string;
  title: string;
  location: string;
  photo_count: number;
  created_at: string;
  cover_url?: string | null;
  output_url: string;
  share_url: string;
  zip_url?: string | null;
  export_urls: string[];
}

export interface Health {
  ok: boolean;
  api_configured: boolean;
  text_model?: string | null;
  image_model: string;
}
