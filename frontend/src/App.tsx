import { FormEvent, memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  CircleStop,
  Download,
  FolderOpen,
  Globe2,
  ImagePlus,
  Images,
  LoaderCircle,
  MapPin,
  Maximize2,
  Pause,
  Play,
  Plus,
  Share2,
  Upload,
  X
} from "lucide-react";
import { api, submitAlbum } from "./api";
import type { AlbumSummary, Health, JobDetail, JobListItem, JobSnapshot, JobUpload } from "./types";

const terminalStatuses = new Set(["paused", "completed", "partial", "failed", "interrupted"]);
const previewLimit = 8;
const stages = [
  { id: "metadata", label: "整理时间" },
  { id: "location", label: "解析地点" },
  { id: "analysis", label: "理解画面" },
  { id: "story", label: "编排故事" },
  { id: "generation", label: "手绘重生" },
  { id: "export", label: "装帧导出" }
];

const stageRanks: Record<string, number> = {
  queued: 0,
  metadata: 0,
  deduplicate: 0,
  location: 1,
  analysis: 2,
  selection: 3,
  story: 3,
  generation: 4,
  generation_retry: 4,
  generation_fallback: 4,
  render: 5,
  export: 5,
  done: 6
};

function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [job, setJob] = useState<JobSnapshot | null>(null);
  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [albums, setAlbums] = useState<AlbumSummary[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [restoredUploads, setRestoredUploads] = useState<JobUpload[]>([]);
  const [showingCurrentTask, setShowingCurrentTask] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [taskActionPending, setTaskActionPending] = useState(false);
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

  const formLocked = showingCurrentTask;
  const localPreviews = useMemo(
    () => files.map((file) => ({
      key: `${file.name}-${file.size}-${file.lastModified}`,
      name: file.name,
      file,
      url: URL.createObjectURL(file)
    })),
    [files]
  );
  const restoredPreviews = useMemo(
    () => restoredUploads.map((upload) => ({
      key: upload.id,
      name: upload.original_name,
      url: upload.preview_url,
      thumbnailUrl: upload.preview_url
    })),
    [restoredUploads]
  );
  const previews = files.length ? localPreviews : restoredPreviews;
  const selectedPhotoCount = previews.length;

  useEffect(
    () => () => localPreviews.forEach((preview) => URL.revokeObjectURL(preview.url)),
    [localPreviews]
  );

  useEffect(() => {
    Promise.all([api.health(), api.currentDetail(), api.jobs(), api.albums()])
      .then(([healthData, currentDetail, jobItems, albumData]) => {
        setHealth(healthData);
        setJobs(jobItems);
        setAlbums(albumData);
        if (currentDetail) {
          hydrateJobDetail(currentDetail);
        }
      })
      .catch((reason: Error) => setError(reason.message));
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      api.jobs().then((items) => {
        setJobs(items);
        setJob((current) => {
          if (!current) return current;
          return items.find((item) => item.snapshot.id === current.id)?.snapshot || current;
        });
      }).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!job || terminalStatuses.has(job.status)) return;
    const events = new EventSource(`/api/jobs/${job.id}/events`);
    events.onmessage = (event) => {
      const next = JSON.parse(event.data) as JobSnapshot;
      setJob(next);
      setJobs((items) => items.map((item) => (
        item.snapshot.id === next.id ? { ...item, snapshot: next } : item
      )));
      if (terminalStatuses.has(next.status)) {
        events.close();
        Promise.all([api.albums(), api.jobs()])
          .then(([albumItems, jobItems]) => {
            setAlbums(albumItems);
            setJobs(jobItems);
          })
          .catch(() => undefined);
      }
    };
    events.onerror = () => events.close();
    return () => events.close();
  }, [job?.id, job?.status]);

  function hydrateJobDetail(detail: JobDetail) {
    setJob(detail.snapshot);
    setSelectedJobId(detail.snapshot.id);
    setTitle(detail.album_input.title);
    setLocation(detail.album_input.location);
    setCompanions(detail.album_input.companions);
    setMemory(detail.album_input.memory);
    setTargetCount(detail.album_input.target_count);
    setFiles([]);
    setRestoredUploads(detail.uploads);
    setShowAllPhotos(false);
    setShowingCurrentTask(true);
  }

  function addFiles(nextFiles: File[]) {
    if (formLocked) return;
    const supported = nextFiles.filter((file) => /\.(jpe?g|png|webp|heic|heif)$/i.test(file.name));
    const unique = new Map<string, File>();
    [...files, ...supported].forEach((file) => unique.set(`${file.name}-${file.size}-${file.lastModified}`, file));
    const merged = [...unique.values()];
    setFiles(merged);
    setRestoredUploads([]);
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
      const [detail, jobItems] = await Promise.all([api.jobDetail(snapshot.id), api.jobs()]);
      hydrateJobDetail(detail);
      setJobs(jobItems);
      setUploadProgress(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务提交失败");
      setUploadProgress(null);
    } finally {
      setSubmitting(false);
    }
  }

  function startNewTask() {
    setFiles([]);
    setRestoredUploads([]);
    setShowingCurrentTask(false);
    setJob(null);
    setSelectedJobId(null);
    setTitle("");
    setLocation("");
    setCompanions("");
    setMemory("");
    setTargetCount(20);
    setShowAllPhotos(false);
    setError("");
  }

  const selectJob = useCallback(async (jobId: string) => {
    setTaskActionPending(true);
    setError("");
    try {
      hydrateJobDetail(await api.jobDetail(jobId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务加载失败");
    } finally {
      setTaskActionPending(false);
    }
  }, []);

  const startCurrentJob = useCallback(async () => {
    if (!job) return;
    setTaskActionPending(true);
    setError("");
    try {
      const snapshot = await api.start(job.id);
      setJob(snapshot);
      setJobs((items) => items.map((item) => (
        item.snapshot.id === snapshot.id ? { ...item, snapshot } : item
      )));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务开始失败");
    } finally {
      setTaskActionPending(false);
    }
  }, [job?.id]);

  const pauseCurrentJob = useCallback(async () => {
    if (!job) return;
    setTaskActionPending(true);
    setError("");
    try {
      const snapshot = await api.pause(job.id);
      setJob(snapshot);
      setJobs((items) => items.map((item) => (
        item.snapshot.id === snapshot.id ? { ...item, snapshot } : item
      )));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务暂停失败");
    } finally {
      setTaskActionPending(false);
    }
  }, [job?.id]);

  const stopGenerationRetries = useCallback(async () => {
    if (!job) return;
    setTaskActionPending(true);
    setError("");
    try {
      setJob(await api.stopRetries(job.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "终止重试失败");
    } finally {
      setTaskActionPending(false);
    }
  }, [job?.id]);

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
            <div><span>{showingCurrentTask ? "CURRENT VOLUME" : "NEW VOLUME"}</span><h2 id="new-album-title">{showingCurrentTask ? "当前任务资料" : "新建旅行手记"}</h2></div>
            <button className="quiet-button" type="button" onClick={startNewTask}><Plus size={16} />新建任务</button>
          </div>

          <form onSubmit={handleSubmit}>
            <div
              className={`dropzone ${dragging ? "is-dragging" : ""} ${selectedPhotoCount ? "has-files" : ""}`}
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragging(false)}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                if (!formLocked) addFiles([...event.dataTransfer.files]);
              }}
            >
              {selectedPhotoCount ? (
                <>
                  <PhotoContactSheet
                    previews={previews}
                    fileCount={selectedPhotoCount}
                    expanded={showAllPhotos}
                    disabled={formLocked}
                    onRemove={removePreviewFile}
                    onToggle={toggleAllPhotos}
                  />
                </>
              ) : (
                <div className="dropzone__empty"><ImagePlus size={30} /><strong>拖入照片或旅行文件夹</strong><span>JPG、PNG、WebP、HEIC</span></div>
              )}
              <div className="dropzone__actions">
                <button type="button" disabled={formLocked} onClick={() => fileInput.current?.click()}><Upload size={17} />选择照片</button>
                <button type="button" disabled={formLocked} onClick={() => folderInput.current?.click()}><FolderOpen size={17} />选择文件夹</button>
              </div>
              <input ref={fileInput} hidden type="file" accept="image/jpeg,image/png,image/webp,.heic,.heif" multiple onChange={(e) => { addFiles([...(e.target.files || [])]); e.currentTarget.value = ""; }} />
              <input ref={folderInput} hidden type="file" accept="image/*,.heic,.heif" multiple {...({ webkitdirectory: "" } as object)} onChange={(e) => { addFiles([...(e.target.files || [])]); e.currentTarget.value = ""; }} />
            </div>

            <div className="field-grid">
              <label className="field field--wide"><span>旅行名称 *</span><input disabled={formLocked} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="2025 年川西自驾" maxLength={120} required /></label>
              <label className="field"><span>地点</span><div className="input-with-icon"><MapPin size={16} /><input disabled={formLocked} value={location} onChange={(e) => setLocation(e.target.value)} placeholder="川西" maxLength={120} /></div></label>
              <label className="field"><span>同行关系</span><input disabled={formLocked} value={companions} onChange={(e) => setCompanions(e.target.value)} placeholder="和父母" maxLength={120} /></label>
              <label className="field field--wide"><span>一句话回忆</span><textarea disabled={formLocked} value={memory} onChange={(e) => setMemory(e.target.value)} placeholder="父亲退休后的第一次远行" maxLength={500} rows={3} /></label>
              <label className="field"><span>目标成片数 *</span><input disabled={formLocked} type="number" min={1} value={targetCount} onChange={(e) => setTargetCount(Math.max(1, Number(e.target.value)))} required /></label>
            </div>

            {error && <div className="form-error">{error}</div>}
            <button className="primary-button" type="submit" disabled={formLocked || submitting || !files.length || !title.trim() || !health?.api_configured}>
              {submitting ? <LoaderCircle className="spin" size={19} /> : <Plus size={19} />}
              {uploadProgress !== null
                ? `正在上传 ${Math.round(uploadProgress)}%`
                : showingCurrentTask
                  ? "当前任务资料已保存"
                  : "创建任务"}
            </button>
          </form>
        </section>

        <aside className="status-column">
          <TaskIndex
            jobs={jobs}
            selectedJobId={selectedJobId}
            loading={taskActionPending}
            onSelect={selectJob}
          />
          <TaskProgress
            job={job}
            actionPending={taskActionPending}
            onStart={startCurrentJob}
            onPause={pauseCurrentJob}
            onStopRetries={stopGenerationRetries}
          />
          <AlbumHistory albums={albums} />
        </aside>
      </main>
    </div>
  );
}

type PhotoPreview = {
  key: string;
  name: string;
  url: string;
  thumbnailUrl?: string;
  file?: File;
};

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
  const thumbnailUrls = useRef(new Map<string, string>());
  const displayedPreviews = expanded ? previews : previews.slice(0, previewLimit);
  const activePreview = previewIndex === null ? null : previews[previewIndex];

  useEffect(() => {
    const currentKeys = new Set(previews.map((preview) => preview.key));
    for (const [key, thumbnailUrl] of thumbnailUrls.current) {
      if (!currentKeys.has(key)) {
        if (thumbnailUrl) URL.revokeObjectURL(thumbnailUrl);
        thumbnailUrls.current.delete(key);
      }
    }

    let cancelled = false;
    const generateThumbnails = async () => {
      for (const { key, file, thumbnailUrl: restoredThumbnail } of displayedPreviews) {
        if (!file || restoredThumbnail || thumbnailUrls.current.has(key)) continue;
        const thumbnailUrl = await createThumbnailUrl(file);
        if (cancelled || !currentKeys.has(key)) {
          if (thumbnailUrl) URL.revokeObjectURL(thumbnailUrl);
          return;
        }
        thumbnailUrls.current.set(key, thumbnailUrl || "");
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
        {displayedPreviews.map((preview, index) => {
          const { key, name, file, url } = preview;
          const thumbnailReady = Boolean(preview.thumbnailUrl) || thumbnailUrls.current.has(key);
          const thumbnailUrl = preview.thumbnailUrl || thumbnailUrls.current.get(key) || url;
          return (
          <div className="contact-sheet__item" key={key}>
            <button
              className="photo-preview"
              type="button"
              onClick={() => setPreviewIndex(index)}
              aria-label={`放大预览 ${name}`}
              title="放大预览"
            >
              {thumbnailReady ? (
                <img src={thumbnailUrl} alt={name} loading="lazy" decoding="async" draggable={false} />
              ) : (
                <span className="photo-preview__loading"><Images size={18} /></span>
              )}
              <span className="photo-zoom-mark"><Maximize2 size={15} /></span>
            </button>
            <button
              className="photo-remove"
              type="button"
              onClick={() => file && onRemove(file)}
              disabled={disabled || !file}
              aria-label={file ? `移除 ${name}` : `${name} 已随任务保存`}
              title={file ? `移除 ${name}` : "任务照片不可修改"}
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
          aria-label={`预览 ${activePreview.name}`}
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
          <img className="photo-lightbox__image" src={activePreview.url} alt={activePreview.name} />
          <div className="photo-lightbox__caption">
            <strong>{activePreview.name}</strong>
            <span>{previewIndex + 1} / {previews.length}</span>
          </div>
        </div>
      )}
    </>
  );
});

const jobStatusLabels: Record<JobSnapshot["status"], string> = {
  queued: "准备中",
  running: "运行中",
  paused: "已暂停",
  completed: "已完成",
  partial: "部分完成",
  failed: "失败",
  interrupted: "已中断"
};

const TaskIndex = memo(function TaskIndex({
  jobs,
  selectedJobId,
  loading,
  onSelect
}: {
  jobs: JobListItem[];
  selectedJobId: string | null;
  loading: boolean;
  onSelect: (jobId: string) => void;
}) {
  return (
    <section className="task-index" aria-labelledby="task-index-title">
      <div className="section-heading task-index__heading">
        <div><span>JOB INDEX</span><h2 id="task-index-title">任务索引</h2></div>
        <strong>{jobs.length}</strong>
      </div>
      {jobs.length === 0 ? (
        <p className="task-index__empty">创建的任务会保存在这里</p>
      ) : (
        <div className="task-index__list">
          {jobs.map((item) => {
            const selected = item.snapshot.id === selectedJobId;
            return (
              <button
                className={`task-index__item ${selected ? "is-selected" : ""}`}
                type="button"
                key={item.snapshot.id}
                onClick={() => onSelect(item.snapshot.id)}
                disabled={loading && !selected}
                aria-current={selected ? "true" : undefined}
              >
                {item.preview_url
                  ? <img src={item.preview_url} alt="" loading="lazy" decoding="async" />
                  : <span className="task-index__blank"><Images size={17} /></span>}
                <span className="task-index__copy">
                  <strong>{item.album_input.title}</strong>
                  <small>{item.upload_count} 张 · {new Date(item.snapshot.updated_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</small>
                </span>
                <span className={`task-status task-status--${item.snapshot.status}`}>{jobStatusLabels[item.snapshot.status]}</span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
});

const TaskProgress = memo(function TaskProgress({
  job,
  actionPending,
  onStart,
  onPause,
  onStopRetries
}: {
  job: JobSnapshot | null;
  actionPending: boolean;
  onStart: () => void;
  onPause: () => void;
  onStopRetries: () => void;
}) {
  const rank = job ? (stageRanks[job.stage] ?? 0) : -1;
  const canStart = job?.status === "paused" || job?.status === "failed" || job?.status === "interrupted";
  const canPause = job?.status === "running";
  return (
    <section className="task-panel" aria-labelledby="task-title">
      <div className="section-heading section-heading--dark">
        <div><span>PROCESS</span><h2 id="task-title">制作进度</h2></div>
        {job && <strong>{Math.round(job.progress)}%</strong>}
      </div>
      {!job ? (
        <div className="task-empty"><span className="empty-frame" /><p>从任务索引选择任务，或新建任务</p></div>
      ) : (
        <>
          <div className="film-progress" style={{ "--progress": `${job.progress}%` } as React.CSSProperties}>
            <div className="film-progress__fill" />
            {stages.map((stage, index) => (
              <div className={`film-step ${index < rank || job.status === "completed" || job.status === "partial" ? "is-done" : ""} ${index === rank && !terminalStatuses.has(job.status) ? "is-active" : ""}`} key={stage.id}>
                <span className="film-step__frame">{index < rank || job.status === "completed" || job.status === "partial" ? <Check size={14} /> : index + 1}</span>
                <span>{stage.label}</span>
              </div>
            ))}
          </div>
          <div className="task-message">
            <strong>{job.message}</strong>
            {job.total_items > 0 && <span>{job.completed_items} / {job.total_items}</span>}
          </div>
          {(job.gps_photo_count > 0 || job.missing_gps_count > 0) && (
            <div className="location-stats">
              <span><MapPin size={13} />GPS {job.gps_photo_count}</span>
              <span>已定位 {job.resolved_location_count}</span>
              <span>无 GPS {job.missing_gps_count}</span>
            </div>
          )}
          {job.stage === "generation_retry" && job.failed_items > 0 && (
            <div className="retry-status">
              <span>第 {job.retry_round} 轮重试</span>
              <strong>{job.failed_items} 张待成功</strong>
            </div>
          )}
          {(job.can_stop_retries || canStart || canPause) && (
            <div className="task-controls">
              {job.can_stop_retries && !job.retry_stop_requested && (
                <button className="task-control task-control--danger" type="button" onClick={onStopRetries} disabled={actionPending}>
                  {actionPending ? <LoaderCircle className="spin" size={17} /> : <CircleStop size={17} />}
                  终止重试并继续
                </button>
              )}
              {canStart && (
                <button className="task-control" type="button" onClick={onStart} disabled={actionPending}>
                  {actionPending ? <LoaderCircle className="spin" size={17} /> : <Play size={17} />}
                  {job.progress > 0 ? "继续执行" : "开始执行"}
                </button>
              )}
              {canPause && (
                <button className="task-control" type="button" onClick={onPause} disabled={actionPending || job.pause_requested}>
                  {actionPending || job.pause_requested ? <LoaderCircle className="spin" size={17} /> : <Pause size={17} />}
                  {job.pause_requested ? "正在暂停" : "暂停任务"}
                </button>
              )}
            </div>
          )}
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
