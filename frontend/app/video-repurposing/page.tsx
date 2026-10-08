'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';

interface Clip {
  clip_id: string;
  title: string;
  reason: string;
  start: number;
  end: number;
  score: number;
  evidence_excerpt: string;
}

interface Job {
  job_id: string;
  status: string;
  stage: string;
  progress: number;
  clips: Clip[];
  error?: string;
  metrics: {
    duration?: number;
    elapsed_seconds?: number;
    candidate_count?: number;
    accepted_clip_count?: number;
  };
}

const API = (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000').replace(/\/$/, '');

function authHeaders(): Record<string, string> {
  const token = window.localStorage.getItem('token');
  if (!token) throw new Error('请先登录，再提交直播切片任务。');
  return { Authorization: 'Bearer ' + token };
}

async function request(path: string, options: RequestInit = {}) {
  const response = await fetch(API + path, {
    ...options,
    headers: { ...authHeaders() },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof body.detail === 'string' ? body.detail : 'HTTP ' + response.status);
  }
  return body;
}

function timestamp(value: number) {
  const s = Math.floor(Math.max(0, value));
  return [Math.floor(s / 3600), Math.floor((s % 3600) / 60), s % 60]
    .map((x) => String(x).padStart(2, '0')).join(':');
}

export default function VideoRepurposingPage() {
  const [file, setFile] = useState<File | null>(null);
  const [objective, setObjective] = useState('寻找观点完整、可以独立传播的直播精彩片段');
  const [jobId, setJobId] = useState('');
  const [job, setJob] = useState<Job | null>(null);
  const [uploading, setUploading] = useState(false);
  const [exporting, setExporting] = useState('');
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    setJobId(window.localStorage.getItem('last_repurposing_job_id') || '');
  }, []);

  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    let timer: ReturnType<typeof setInterval> | undefined;
    const poll = async () => {
      try {
        const result: Job = await request('/api/v1/video-repurposing/jobs/' + encodeURIComponent(jobId));
        if (stopped) return;
        setJob(result);
        if (result.status === 'failed') setError(result.error || '分析失败，可尝试恢复任务。');
        if (['ready', 'failed', 'cancelled'].includes(result.status) && timer) clearInterval(timer);
      } catch (e) {
        if (!stopped) setError(e instanceof Error ? e.message : '进度查询失败');
      }
    };
    void poll();
    timer = setInterval(poll, 2500);
    return () => {
      stopped = true;
      if (timer) clearInterval(timer);
    };
  }, [jobId, revision]);

  async function upload(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || uploading) return;
    setError('');
    setUploading(true);
    try {
      const data = new FormData();
      data.append('file', file);
      data.append('objective', objective);
      const result = await request('/api/v1/video-repurposing/jobs', { method: 'POST', body: data });
      setJob(null);
      setJobId(result.job_id);
      window.localStorage.setItem('last_repurposing_job_id', result.job_id);
      setRevision((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : '上传失败');
    } finally {
      setUploading(false);
    }
  }

  async function resume() {
    setError('');
    try {
      await request('/api/v1/video-repurposing/jobs/' + encodeURIComponent(jobId) + '/resume', { method: 'POST' });
      setRevision((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : '恢复失败');
    }
  }

  async function exportOne(clip: Clip) {
    if (exporting) return;
    setExporting(clip.clip_id);
    setError('');
    const endpoint = '/api/v1/video-repurposing/jobs/' + encodeURIComponent(jobId)
      + '/clips/' + encodeURIComponent(clip.clip_id);
    try {
      await request(endpoint + '/export', { method: 'POST' });
      const result = await fetch(API + endpoint + '/download', { headers: authHeaders() });
      if (!result.ok) throw new Error('下载失败：HTTP ' + result.status);
      const url = URL.createObjectURL(await result.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = clip.clip_id + '.mp4';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      setError(e instanceof Error ? e.message : '切片导出失败');
    } finally {
      setExporting('');
    }
  }

  return (
    <main className="min-h-screen bg-[#0a0b10] text-white">
      <header className="border-b border-white/10 bg-black/50">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-5">
          <div>
            <h1 className="text-xl font-semibold">直播智能切片</h1>
            <p className="mt-1 text-xs text-white/40">Multimodal Agent / Long-video Repurposing</p>
          </div>
          <Link href="/control-center" className="text-sm text-sky-300 hover:text-white">返回控制中心</Link>
        </div>
      </header>

      <div className="mx-auto grid max-w-6xl gap-6 px-6 py-8 lg:grid-cols-[350px_1fr]">
        <aside className="space-y-5">
          <form onSubmit={upload} className="space-y-4 rounded-2xl border border-white/10 bg-white/[0.03] p-5">
            <h2 className="font-medium">创建新任务</h2>
            <p className="text-sm text-white/50">上传本地直播回放，AI 自动生成有时间戳的候选切片。</p>
            <label className="flex cursor-pointer flex-col items-center rounded-xl border border-dashed border-white/20 p-6 text-center text-sm hover:border-sky-400">
              <span className="break-all">{file?.name || '选择 MP4 / MOV / MKV / WebM 视频'}</span>
              <span className="mt-2 text-xs text-white/40">{file ? (file.size / 1048576).toFixed(1) + ' MB' : '可选音频文件用于内容分析'}</span>
              <input type="file" accept=".mp4,.mov,.mkv,.webm,.mp3,.m4a,.wav" className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] || null)} />
            </label>
            <label htmlFor="objective" className="block text-sm text-white/70">剪辑目标</label>
            <textarea id="objective" value={objective} maxLength={600} rows={4}
              onChange={(e) => setObjective(e.target.value)}
              className="w-full rounded-lg border border-white/15 bg-black/30 p-3 text-sm outline-none focus:border-sky-400" />
            <button disabled={uploading || !file} className="w-full rounded-lg bg-sky-500 px-4 py-3 font-medium text-black disabled:opacity-40">
              {uploading ? '正在上传…' : '开始智能分析'}
            </button>
          </form>
          {job && (
            <div className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
              <div className="flex justify-between text-sm">
                <span>{job.stage}</span><span>{job.progress}%</span>
              </div>
              <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/10">
                <div className="h-full bg-sky-400" style={{ width: String(job.progress) + '%' }} />
              </div>
              <p className="mt-3 break-all font-mono text-xs text-white/40">{job.job_id}</p>
              {job.status === 'failed' &&
                <button onClick={() => void resume()} className="mt-3 text-sm text-sky-300">从检查点恢复</button>}
            </div>
          )}
          {error && <p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">{error}</p>}
        </aside>

        <section className="space-y-4">
          <div className="flex items-end justify-between">
            <div>
              <h2 className="text-xl font-semibold">候选精彩片段</h2>
              <p className="mt-1 text-sm text-white/40">人工审核建议后再导出；系统不会自动发布</p>
            </div>
            {job?.status === 'ready' && <span className="text-sm text-sky-300">{job.clips.length} 个候选</span>}
          </div>
          {!job && <div className="rounded-2xl border border-white/10 p-12 text-center text-white/40">选择视频后开始分析，结果会显示在这里。</div>}
          {job?.status === 'ready' && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {[
                ['视频时长', timestamp(job.metrics.duration || 0)],
                ['分析耗时', (job.metrics.elapsed_seconds || 0).toFixed(1) + 's'],
                ['原始建议', String(job.metrics.candidate_count ?? 0)],
                ['有效建议', String(job.metrics.accepted_clip_count ?? 0)],
              ].map(([label, value]) =>
                <div key={label} className="rounded-xl border border-white/10 p-3">
                  <p className="text-xs text-white/40">{label}</p><p className="mt-2 font-semibold">{value}</p>
                </div>)}
            </div>
          )}
          {job?.status === 'ready' && job.clips.length === 0 &&
            <p className="rounded-xl border border-white/10 p-8 text-center text-sm text-white/50">没有找到符合时长与证据要求的片段。</p>}
          {job?.clips?.map((clip) =>
            <article key={clip.clip_id} className="rounded-2xl border border-white/10 bg-white/[0.03] p-5">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div>
                  <h3 className="font-medium">{clip.title}</h3>
                  <p className="mt-2 font-mono text-xs text-sky-300">
                    {timestamp(clip.start)} — {timestamp(clip.end)}
                    <span className="ml-3 text-white/40">质量评分 {Math.round(clip.score * 100)}%</span>
                  </p>
                </div>
                <button disabled={Boolean(exporting)} onClick={() => void exportOne(clip)}
                  className="rounded-lg border border-sky-400/30 px-3 py-2 text-sm text-sky-300 disabled:opacity-40">
                  {exporting === clip.clip_id ? '正在生成 MP4…' : '导出 MP4'}
                </button>
              </div>
              <p className="mt-3 text-sm text-white/70">{clip.reason}</p>
              <p className="mt-3 rounded-lg bg-black/30 p-3 text-xs text-white/40">ASR 证据：{clip.evidence_excerpt}</p>
            </article>)}
        </section>
      </div>
    </main>
  );
}
