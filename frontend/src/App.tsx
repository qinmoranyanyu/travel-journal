import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  Archive,
  ArrowUpRight,
  Check,
  Download,
  FolderOpen,
  ImagePlus,
  Images,
  LoaderCircle,
  MapPin,
  RotateCcw,
  Sparkles,
  Upload
} from "lucide-react";
import { api, submitAlbum } from "./api";
import type { AlbumSummary, Health, JobSnapshot } from "./types";

const terminalStatuses = new Set(["completed", "partial", "failed", "interrupted"]);
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
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);

  const active = Boolean(job && !terminalStatuses.has(job.status));
  const previews = useMemo(
    () => files.slice(0, 8).map((file) => ({ file, url: URL.createObjectURL(file) })),
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
                  <div className="contact-sheet">
                    {previews.map(({ file, url }) => <img key={`${file.name}-${file.lastModified}`} src={url} alt="" />)}
                    {files.length > 8 && <div className="more-photos">+{files.length - 8}</div>}
                  </div>
                  <div className="dropzone__summary"><Images size={19} /><strong>{files.length} 张照片</strong><span>已准备</span></div>
                </>
              ) : (
                <div className="dropzone__empty"><ImagePlus size={30} /><strong>拖入照片或旅行文件夹</strong><span>JPG、PNG、WebP、HEIC</span></div>
              )}
              <div className="dropzone__actions">
                <button type="button" onClick={() => fileInput.current?.click()}><Upload size={17} />选择照片</button>
                <button type="button" onClick={() => folderInput.current?.click()}><FolderOpen size={17} />选择文件夹</button>
              </div>
              <input ref={fileInput} hidden type="file" accept="image/jpeg,image/png,image/webp,.heic,.heif" multiple onChange={(e) => addFiles([...(e.target.files || [])])} />
              <input ref={folderInput} hidden type="file" accept="image/*,.heic,.heif" multiple {...({ webkitdirectory: "" } as object)} onChange={(e) => addFiles([...(e.target.files || [])])} />
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

function TaskProgress({ job }: { job: JobSnapshot | null }) {
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
          {(job.output_url || job.zip_url) && (
            <div className="result-actions">
              {job.output_url && <a href={job.output_url} target="_blank" rel="noreferrer"><ArrowUpRight size={17} />打开相册</a>}
              {job.zip_url && <a href={job.zip_url} download><Download size={17} />下载 ZIP</a>}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function AlbumHistory({ albums }: { albums: AlbumSummary[] }) {
  return (
    <section className="history" aria-labelledby="history-title">
      <div className="section-heading"><div><span>ARCHIVE</span><h2 id="history-title">最近成册</h2></div><strong>{albums.length}</strong></div>
      <div className="history-list">
        {albums.length === 0 && <p className="history-empty">完成的相册会出现在这里</p>}
        {albums.slice(0, 6).map((album) => (
          <article className="album-item" key={album.id}>
            {album.cover_url ? <img src={album.cover_url} alt="" /> : <div className="album-item__blank" />}
            <div><span>{new Date(album.created_at).toLocaleDateString("zh-CN")} · {album.photo_count} 张</span><h3>{album.title}</h3><p>{album.location || "沿途手记"}</p></div>
            <a href={album.output_url} target="_blank" rel="noreferrer" aria-label={`打开${album.title}`} title="打开相册"><ArrowUpRight size={18} /></a>
          </article>
        ))}
      </div>
    </section>
  );
}

export default App;
