import type { AlbumSummary, Health, JobSnapshot } from "./types";

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`请求失败 (${response.status})`);
  return response.json() as Promise<T>;
}

export const api = {
  health: () => getJson<Health>("/api/health"),
  current: () => getJson<JobSnapshot | null>("/api/jobs/current"),
  albums: () => getJson<AlbumSummary[]>("/api/albums")
};

export function submitAlbum(
  formData: FormData,
  onUploadProgress: (progress: number) => void
): Promise<JobSnapshot> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/jobs");
    request.responseType = "json";
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onUploadProgress((event.loaded / event.total) * 100);
    });
    request.addEventListener("load", () => {
      if (request.status >= 200 && request.status < 300) {
        resolve(request.response as JobSnapshot);
      } else {
        reject(new Error(request.response?.detail || `提交失败 (${request.status})`));
      }
    });
    request.addEventListener("error", () => reject(new Error("无法连接本地服务")));
    request.send(formData);
  });
}
