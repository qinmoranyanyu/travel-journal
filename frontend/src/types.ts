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
  zip_url?: string | null;
  export_urls: string[];
}

export interface AlbumSummary {
  id: string;
  title: string;
  location: string;
  photo_count: number;
  created_at: string;
  cover_url?: string | null;
  output_url: string;
  zip_url?: string | null;
  export_urls: string[];
}

export interface Health {
  ok: boolean;
  api_configured: boolean;
  text_model?: string | null;
  image_model: string;
}
