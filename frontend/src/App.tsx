import { FormEvent, memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Download,
  FolderOpen,
  Globe2,
  ImagePlus,
  Images,
  LoaderCircle,
  MapPin,
  Maximize2,
  RotateCcw,
  Share2,
  Sparkles,
  Upload,
  X
} from "lucide-react";
import { api, submitAlbum } from "./api";
import type { AlbumSummary, Health, JobSnapshot } from "./types";

const terminalStatuses = new Set(["completed", "partial", "failed", "interrupted"]);
const previewLimit = 8;
const stages = [
  { id: "metadata", label: "整理时间" },
  { id: "analysis", label: "理解画面" },
  { id: "story", label: "编排故事" },
  { id: "generation", label: "手绘重生" },
  { id: "export", label: "装帧导出" }
];

const stageRanks: Record<string, number> = {
  queued: 0,
  metadata: 0,
  deduplicate: 0,
  analysis: 1,
  selection: 2,
  story: 2,
  generation: 3,
  render: 4,
  export: 4,
  done: 5
};

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [job, setJob] = useState<JobSnapshot | null>(null);
  const [albums, setAlbums] = useState<AlbumSummary[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [title, setTitle] = useState("");
  const [location, setLocation] = useState("");
  const [companions, setCompanions] = useState("");
  const [memory, setMemory] = useState("");
  const [targetCount, setTargetCount] = useState(20);
  const [showAllPhotos, setShowAllPhotos] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  const active = Boolean(job && !terminalStatuses.has(job.status));
  const previews = useMemo(
    () => files.map((file) => ({ file, url: URL.createObjectURL(file) })),
    [files]
  );

  useEffect(() => () => previews.forEach((preview) => URL.revokeObjectURL(preview.url)), [previews]);

  useEffect(() => {
    Promise.all([api.health(), api.current(), api.albums()])
      .then(([healthData, currentJob, albumData]) => {
        setHealth(healthData);
        setJob(currentJob);
        setAlbums(albumData);
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  useEffect(() => {
    if (!job || terminalStatuses.has(job.status)) return;
    const events = new EventSource(`/api/jobs/${job.id}/events`);
    events.onmessage = (event) => {
      const next = JSON.parse(event.data) as JobSnapshot;
      setJob(next);
      if (terminalStatuses.has(next.status)) {
        events.close();
        api.albums().then(setAlbums).catch(() => undefined);
      }
    };
    events.onerror = () => events.close();
    return () => events.close();
  }, [job?.id, job?.status]);

  function addFiles(nextFiles: File[]) {
    const supported = nextFiles.filter((file) => /\.(jpe?g|png|webp|heic|heif)$/i.test(file.name));
    const unique = new Map<string, File>();
    [...files, ...supported].forEach((file) => unique.set(`${file.name}-${file.size}-${file.lastModified}`, file));
    const merged = [...unique.values()];
    setFiles(merged);
    if (!files.length) setTargetCount(Math.max(1, Math.min(20, merged.length)));
    setError(supported.length ? "" : "未找到支持的图片格式");
  }

  function removeFile(fileToRemove: File) {
    const remaining = files.filter((file) => file !== fileToRemove);
    setFiles(remaining);
    setTargetCount((count) => Math.max(1, Math.min(count, remaining.length || 1)));
    if (remaining.length <= previewLimit) setShowAllPhotos(false);
  }

  const removePreviewFile = useCallback((fileToRemove: File) => {
    removeFile(fileToRemove);
  }, [files]);

  const toggleAllPhotos = useCallback(() => {
    setShowAllPhotos((visible) => !visible);
  }, []);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!files.length || !title.trim()) return;
    setSubmitting(true);
    setError("");
    setUploadProgress(0);
    const data = new FormData();
    data.append("title", title.trim());
    data.append("location", location.trim());
    data.append("companions", companions.trim());
    data.append("memory", memory.trim());
    data.append("target_count", String(targetCount));
    files.forEach((file) => data.append("photos", file, file.name));
    data.append(
      "file_metadata",
      JSON.stringify(files.map((file, order) => ({ name: file.name, order, lastModified: file.lastModified })))
    );
    try {
      const snapshot = await submitAlbum(data, setUploadProgress);
      setJob(snapshot);
      setUploadProgress(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务提交失败");
      setUploadProgress(null);
    } finally {
      setSubmitting(false);
    }
  }

  function resetForm() {
    setFiles([]);
    setTitle("");
    setLocation("");
    setCompanions("");
    setMemory("");
    setTargetCount(20);
    setShowAllPhotos(false);
    setError("");
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-mark"><Archive size={19} strokeWidth={1.8} /></div>
        <div>
          <p>LOCAL PHOTO ARCHIVE</p>
          <h1>旅迹编年</h1>
        </div>
        <div className={`api-state ${health?.api_configured ? "is-ready" : ""}`}>
          <span />{health?.api_configured ? `${health.text_model} / ${health.image_model}` : "等待 API 配置"}
        </div>
      </header>

      {!health?.api_configured && health && (
        <div className="config-alert">请先在项目根目录的 <code>.env</code> 中填写 API 密钥和文本模型。</div>
      )}

      <main className="workspace">
        <section className="composer" aria-labelledby="new-album-title">
          <div className="section-heading">
            <div><span>NEW VOLUME</span><h2 id="new-album-title">新建旅行手记</h2></div>
            {files.length > 0 && <button className="quiet-button" type="button" onClick={resetForm} disabled={active}><RotateCcw size={16} />清空</button>}
          </div>

          <form onSubmit={handleSubmit}>
            <div
              className={`dropzone ${dragging ? "is-dragging" : ""} ${files.length ? "has-files" : ""}`}
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                addFiles([...event.dataTransfer.files]);
              }}
            >
              {files.length ? (
                <>
                  <PhotoContactSheet
                    previews={previews}
                    fileCount={files.length}
                    expanded={showAllPhotos}
                    disabled={active}
                    onRemove={removePreviewFile}
                    onToggle={toggleAllPhotos}
                  />
                </>
              ) : (
                <div className="dropzone__empty"><ImagePlus size={30} /><strong>拖入照片或旅行文件夹</strong><span>JPG、PNG、WebP、HEIC</span></div>
              )}
              <div className="dropzone__actions">
                <button type="button" onClick={() => fileInput.current?.click()}><Upload size={17} />选择照片</button>
                <button type="button" onClick={() => folderInput.current?.click()}><FolderOpen size={17} />选择文件夹</button>
              </div>
              <input ref={fileInput} hidden type="file" accept="image/jpeg,image/png,image/webp,.heic,.heif" multiple onChange={(e) => { addFiles([...(e.target.files || [])]); e.currentTarget.value = ""; }} />
              <input ref={folderInput} hidden type="file" accept="image/*,.heic,.heif" multiple {...({ webkitdirectory: "" } as object)} onChange={(e) => { addFiles([...(e.target.files || [])]); e.currentTarget.value = ""; }} />
            </div>

            <div className="field-grid">
              <label className="field field--wide"><span>旅行名称 *</span><input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="2025 年川西自驾" maxLength={120} required /></label>
              <label className="field"><span>地点</span><div className="input-with-icon"><MapPin size={16} /><input value={location} onChange={(e) => setLocation(e.target.value)} placeholder="川西" maxLength={120} /></div></label>
              <label className="field"><span>同行关系</span><input value={companions} onChange={(e) => setCompanions(e.target.value)} placeholder="和父母" maxLength={120} /></label>
              <label className="field field--wide"><span>一句话回忆</span><textarea value={memory} onChange={(e) => setMemory(e.target.value)} placeholder="父亲退休后的第一次远行" maxLength={500} rows={3} /></label>
              <label className="field"><span>目标成片数 *</span><input type="number" min={1} value={targetCount} onChange={(e) => setTargetCount(Math.max(1, Number(e.target.value)))} required /></label>
            </div>

            {error && <div className="form-error">{error}</div>}
            <button className="primary-button" type="submit" disabled={active || submitting || !files.length || !title.trim() || !health?.api_configured}>
              {submitting ? <LoaderCircle className="spin" size={19} /> : <Sparkles size={19} />}
              {uploadProgress !== null ? `正在上传 ${Math.round(uploadProgress)}%` : active ? "当前任务进行中" : "开始生成手记"}
            </button>
          </form>
        </section>

        <aside className="status-column">
          <TaskProgress job={job} />
          <AlbumHistory albums={albums} />
        </aside>
      </main>
    </div>
  );
}

type PhotoPreview = { file: File; url: string };

async function createThumbnailUrl(file: File): Promise<string | null> {
  let bitmap: ImageBitmap | null = null;
  try {
    bitmap = await createImageBitmap(file, {
      resizeWidth: 640,
      resizeQuality: "medium"
    });
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext("2d", { alpha: false });
    if (!context) return null;
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) => {
      canvas.toBlob(resolve, "image/jpeg", 0.78);
    });
    return blob ? URL.createObjectURL(blob) : null;
  } catch {
    return null;
  } finally {
    bitmap?.close();
  }
}

const PhotoContactSheet = memo(function PhotoContactSheet({
  previews,
  fileCount,
  expanded,
  disabled,
  onRemove,
  onToggle
}: {
  previews: PhotoPreview[];
  fileCount: number;
  expanded: boolean;
  disabled: boolean;
  onRemove: (file: File) => void;
  onToggle: () => void;
}) {
  const [previewIndex, setPreviewIndex] = useState<number | null>(null);
  const [, setThumbnailVersion] = useState(0);
  const thumbnailUrls = useRef(new Map<File, string>());
  const displayedPreviews = expanded ? previews : previews.slice(0, previewLimit);
  const activePreview = previewIndex === null ? null : previews[previewIndex];

  useEffect(() => {
    const currentFiles = new Set(previews.map(({ file }) => file));
    for (const [file, thumbnailUrl] of thumbnailUrls.current) {
      if (!currentFiles.has(file)) {
        if (thumbnailUrl) URL.revokeObjectURL(thumbnailUrl);
        thumbnailUrls.current.delete(file);
      }
    }

    let cancelled = false;
    const generateThumbnails = async () => {
      for (const { file } of displayedPreviews) {
        if (thumbnailUrls.current.has(file)) continue;
        const thumbnailUrl = await createThumbnailUrl(file);
        if (cancelled || !currentFiles.has(file)) {
          if (thumbnailUrl) URL.revokeObjectURL(thumbnailUrl);
          return;
        }
        thumbnailUrls.current.set(file, thumbnailUrl || "");
        setThumbnailVersion((version) => version + 1);
        await new Promise((resolve) => window.setTimeout(resolve, 0));
      }
    };
    void generateThumbnails();
    return () => {
      cancelled = true;
    };
  }, [previews, expanded]);

  useEffect(() => () => {
    for (const thumbnailUrl of thumbnailUrls.current.values()) {
      if (thumbnailUrl) URL.revokeObjectURL(thumbnailUrl);
    }
    thumbnailUrls.current.clear();
  }, []);

  useEffect(() => {
    if (previewIndex !== null && previewIndex >= previews.length) {
      setPreviewIndex(previews.length ? previews.length - 1 : null);
    }
  }, [previewIndex, previews.length]);

  useEffect(() => {
    if (previewIndex === null) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPreviewIndex(null);
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        setPreviewIndex((current) => current === null ? null : (current - 1 + previews.length) % previews.length);
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        setPreviewIndex((current) => current === null ? null : (current + 1) % previews.length);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [previewIndex, previews.length]);

  return (
    <>
      <div className="contact-sheet">
        {displayedPreviews.map(({ file, url }, index) => {
          const thumbnailReady = thumbnailUrls.current.has(file);
          const thumbnailUrl = thumbnailUrls.current.get(file) || url;
          return (
          <div className="contact-sheet__item" key={`${file.name}-${file.size}-${file.lastModified}`}>
            <button
              className="photo-preview"
              type="button"
              onClick={() => setPreviewIndex(index)}
              aria-label={`放大预览 ${file.name}`}
              title="放大预览"
            >
              {thumbnailReady ? (
                <img src={thumbnailUrl} alt={file.name} loading="lazy" decoding="async" draggable={false} />
              ) : (
                <span className="photo-preview__loading"><Images size={18} /></span>
              )}
              <span className="photo-zoom-mark"><Maximize2 size={15} /></span>
            </button>
            <button
              className="photo-remove"
              type="button"
              onClick={() => onRemove(file)}
              disabled={disabled}
              aria-label={`移除 ${file.name}`}
              title={`移除 ${file.name}`}
            >
              <X size={15} />
            </button>
          </div>
          );
        })}
      </div>
      {fileCount > previewLimit && (
        <button
          className="photo-toggle"
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
        >
          {expanded ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
          {expanded ? `收起到前 ${previewLimit} 张` : `展开其余 ${fileCount - previewLimit} 张`}
        </button>
      )}
      <div className="dropzone__summary" aria-live="polite"><Images size={19} /><strong>{fileCount} 张照片</strong><span>已准备</span></div>

      {activePreview && previewIndex !== null && (
        <div
          className="photo-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={`预览 ${activePreview.file.name}`}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setPreviewIndex(null);
          }}
        >
          <button className="lightbox-button lightbox-button--close" type="button" onClick={() => setPreviewIndex(null)} aria-label="关闭预览" title="关闭预览" autoFocus><X size={22} /></button>
          {previews.length > 1 && (
            <>
              <button className="lightbox-button lightbox-button--previous" type="button" onClick={() => setPreviewIndex((previewIndex - 1 + previews.length) % previews.length)} aria-label="上一张" title="上一张"><ChevronLeft size={26} /></button>
              <button className="lightbox-button lightbox-button--next" type="button" onClick={() => setPreviewIndex((previewIndex + 1) % previews.length)} aria-label="下一张" title="下一张"><ChevronRight size={26} /></button>
            </>
          )}
          <img className="photo-lightbox__image" src={activePreview.url} alt={activePreview.file.name} />
          <div className="photo-lightbox__caption">
            <strong>{activePreview.file.name}</strong>
            <span>{previewIndex + 1} / {previews.length}</span>
          </div>
        </div>
      )}
    </>
  );
});

const TaskProgress = memo(function TaskProgress({ job }: { job: JobSnapshot | null }) {
  const rank = job ? (stageRanks[job.stage] ?? 0) : -1;
  return (
    <section className="task-panel" aria-labelledby="task-title">
      <div className="section-heading section-heading--dark">
        <div><span>PROCESS</span><h2 id="task-title">制作进度</h2></div>
        {job && <strong>{Math.round(job.progress)}%</strong>}
      </div>
      {!job ? (
        <div className="task-empty"><span className="empty-frame" /><p>还没有正在制作的相册</p></div>
      ) : (
        <>
          <div className="film-progress" style={{ "--progress": `${job.progress}%` } as React.CSSProperties}>
            <div className="film-progress__fill" />
            {stages.map((stage, index) => (
              <div className={`film-step ${index < rank || job.status === "completed" ? "is-done" : ""} ${index === rank && !terminalStatuses.has(job.status) ? "is-active" : ""}`} key={stage.id}>
                <span className="film-step__frame">{index < rank || job.status === "completed" ? <Check size={14} /> : index + 1}</span>
                <span>{stage.label}</span>
              </div>
            ))}
          </div>
          <div className="task-message">
            <strong>{job.message}</strong>
            {job.total_items > 0 && <span>{job.completed_items} / {job.total_items}</span>}
          </div>
          {job.error && <div className="task-error">{job.error}</div>}
          {(job.share_url || job.output_url || job.zip_url) && (
            <div className="result-actions">
              {job.share_url && <a href={job.share_url} target="_blank" rel="noreferrer"><Share2 size={17} />分享页面</a>}
              {job.output_url && <a href={job.output_url} target="_blank" rel="noreferrer"><Globe2 size={17} />Web 页面</a>}
              {job.zip_url && <a href={job.zip_url} download><Download size={17} />导出 ZIP</a>}
            </div>
          )}
        </>
      )}
    </section>
  );
});

const AlbumHistory = memo(function AlbumHistory({ albums }: { albums: AlbumSummary[] }) {
  return (
    <section className="history" aria-labelledby="history-title">
      <div className="section-heading"><div><span>ARCHIVE</span><h2 id="history-title">最近成册</h2></div><strong>{albums.length}</strong></div>
      <div className="history-list">
        {albums.length === 0 && <p className="history-empty">完成的相册会出现在这里</p>}
        {albums.slice(0, 6).map((album) => (
          <article className="album-item" key={album.id}>
            {album.cover_url ? <img src={album.cover_url} alt="" /> : <div className="album-item__blank" />}
            <div><span>{new Date(album.created_at).toLocaleDateString("zh-CN")} · {album.photo_count} 张</span><h3>{album.title}</h3><p>{album.location || "沿途手记"}</p></div>
            <div className="album-item__actions">
              <a href={album.share_url} target="_blank" rel="noreferrer" aria-label={`打开${album.title}分享页面`} title="打开分享页面"><Share2 size={17} /></a>
              <a href={album.output_url} target="_blank" rel="noreferrer" aria-label={`打开${album.title} Web 页面`} title="打开 Web 页面"><Globe2 size={17} /></a>
              {album.zip_url && <a href={album.zip_url} download aria-label={`导出${album.title} ZIP`} title="导出 ZIP"><Download size={17} /></a>}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
});

export default App;
