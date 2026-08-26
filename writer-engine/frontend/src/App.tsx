import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BookOpenCheck, FileDown, FileJson, FileUp, GitCompare, Save, ScanText, Sparkles } from 'lucide-react';
import './styles.css';
import { selectionRange } from './offsets.js';

type Flag = { analyzer?: string; type: string; severity: string; source?: string; start: number; end: number; start_utf16?: number; end_utf16?: number; excerpt: string; suggestion: string };
type Analysis = { name: string; flags: Flag[] };
type Report = {
  run_id?: string;
  run_dir?: string;
  output_text?: string;
  score_before?: number;
  score_after?: number;
  llm_errors?: string[];
  analysis_after?: Analysis[];
  analysis_before?: Analysis[];
  document_count?: number;
  manuscript_stats?: any;
  chapters?: any[];
  repetition?: any;
  pattern_hotspots?: any[];
};
type Provider = { provider: string; base_url: string; api_key: string; model: string; temperature: number; max_tokens: number };

const defaultProvider: Provider = {
  provider: 'openai_compatible',
  base_url: 'http://127.0.0.1:1234/v1',
  api_key: '',
  model: 'local-model',
  temperature: 0.7,
  max_tokens: 4096,
};

const defaultFilterCategories: Record<string, string[]> = {
  perception: ['saw', 'heard', 'felt', 'noticed', 'watched'],
  cognitive: ['realized', 'knew', 'thought', 'wondered', 'remembered', 'decided'],
  distancing: ['could see', 'could hear', 'began to', 'started to'],
  hedges: ['just', 'really', 'quite', 'rather', 'somewhat', 'perhaps'],
  vague_modifiers: ['very', 'somehow', 'almost', 'slightly', 'a little', 'a bit'],
};

function App() {
  const [tab, setTab] = useState('editor');
  const [profiles, setProfiles] = useState<any[]>([]);
  const [profile, setProfile] = useState('creative-default');
  const [profileDraft, setProfileDraft] = useState<any | null>(null);
  const [mode, setMode] = useState('rewrite');
  const [passes, setPasses] = useState(1);
  const [provider, setProvider] = useState<Provider>(() => {
    const saved = JSON.parse(localStorage.getItem('thothpad-provider')
      || localStorage.getItem('writer-provider') || 'null');
    return { ...defaultProvider, ...(saved || {}), api_key: '' };
  });
  const [input, setInput] = useState('');
  const [output, setOutput] = useState('');
  const [report, setReport] = useState<Report | null>(null);
  const [status, setStatus] = useState('');
  const [sourceName, setSourceName] = useState('thothpad-output');
  const [preserveText, setPreserveText] = useState('');
  const [projectName, setProjectName] = useState('');
  const [projects, setProjects] = useState<any[]>([]);
  const [voiceName, setVoiceName] = useState('new-voice-profile');
  const [voiceSamples, setVoiceSamples] = useState<string[]>([]);
  const [batchItems, setBatchItems] = useState<{ name: string; text: string; report?: Report }[]>([]);
  const [manuscriptItems, setManuscriptItems] = useState<{ name: string; text: string }[]>([]);
  const [manuscriptReport, setManuscriptReport] = useState<Report | null>(null);
  const [manuscriptProject, setManuscriptProject] = useState('');
  const [integrations, setIntegrations] = useState<any>({});
  const [calibrationName, setCalibrationName] = useState('local-model-fiction');
  const [calibrationSamples, setCalibrationSamples] = useState<string[]>([]);
  const [calibrationReferences, setCalibrationReferences] = useState<string[]>([]);
  const [calibrationResult, setCalibrationResult] = useState<any | null>(null);
  const [agentSetup, setAgentSetup] = useState<any | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const batchInputRef = useRef<HTMLInputElement | null>(null);
  const voiceInputRef = useRef<HTMLInputElement | null>(null);
  const manuscriptInputRef = useRef<HTMLInputElement | null>(null);
  const calibrationInputRef = useRef<HTMLInputElement | null>(null);
  const calibrationReferenceRef = useRef<HTMLInputElement | null>(null);
  const outputRef = useRef<HTMLTextAreaElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    refreshProfiles();
    refreshProjects();
    fetch('/api/agent-setup').then((r) => r.json()).then(setAgentSetup);
    fetch('/api/integrations').then((r) => r.json()).then(setIntegrations);
  }, []);
  useEffect(() => {
    const { api_key: _discarded, ...nonSecretProvider } = provider;
    localStorage.setItem('thothpad-provider', JSON.stringify(nonSecretProvider));
  }, [provider]);
  useEffect(() => {
    const found = profiles.find((row) => row.name === profile)?.profile;
    if (found) setProfileDraft(JSON.parse(JSON.stringify(found)));
  }, [profile, profiles]);

  async function refreshProfiles() {
    const rows = await fetch('/api/profiles').then((r) => r.json());
    setProfiles(rows);
    if (!rows.some((row: any) => row.name === profile) && rows[0]) setProfile(rows[0].name);
  }

  async function refreshProjects() {
    setProjects(await fetch('/api/projects').then((r) => r.json()));
  }

  const flags = useMemo(() => {
    const analysis = report?.analysis_after || report?.analysis_before || [];
    return analysis.flatMap((result) => (result.flags || []).map((flag) => ({ ...flag, analyzer: result.name })));
  }, [report]);

  const preserve = useMemo(() => preserveText.split(/\r?\n/).map((x) => x.trim()).filter(Boolean), [preserveText]);

  async function call(url: string, body: any) {
    setStatus('Working...');
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    setReport(data);
    if (data.output_text) setOutput(data.output_text);
    setStatus(data.run_id ? `Saved run ${data.run_id}${data.llm_errors?.length ? ' - LLM unavailable' : ''}` : '');
  }

  function runBody(extra: any = {}) {
    return { text: input, profile, mode, passes, provider, preserve, ...extra };
  }

  function baseName(name: string) {
    return name.replace(/\.[^.]+$/, '').replace(/[^\w.-]+/g, '-').replace(/^-+|-+$/g, '') || 'thothpad-output';
  }

  async function importFile(file: File | undefined) {
    if (!file) return;
    setInput(await file.text());
    setSourceName(baseName(file.name));
    setStatus(`Imported ${file.name}`);
  }

  async function importMany(files: FileList | null, setter: (items: any) => void) {
    if (!files) return;
    const rows = [];
    for (const file of Array.from(files).filter((f) => /\.(txt|md|markdown)$/i.test(f.name))) {
      rows.push({ name: file.name, text: await file.text() });
    }
    setter(rows);
    setStatus(`Imported ${rows.length} files`);
  }

  async function saveText(text: string, filename: string, mime: string) {
    if (!text.trim()) { setStatus('Nothing to save.'); return; }
    const picker = (window as any).showSaveFilePicker;
    if (picker) {
      const ext = filename.slice(filename.lastIndexOf('.'));
      const handle = await picker({ suggestedName: filename, types: [{ description: mime, accept: { [mime]: [ext] } }] });
      const writable = await handle.createWritable();
      await writable.write(text);
      await writable.close();
      setStatus(`Saved ${filename}`);
      return;
    }
    const blob = new Blob([text], { type: mime });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
    setStatus(`Downloaded ${filename}`);
  }

  function jumpToFlag(flag: Flag) {
    const target = outputRef.current || inputRef.current;
    if (!target) return;
    target.focus();
    const [start, end] = selectionRange(flag);
    target.setSelectionRange(start, end);
  }

  async function saveProfile() {
    if (!profileDraft) return;
    const res = await fetch(`/api/profiles/${encodeURIComponent(profileDraft.name || profile)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile: profileDraft }),
    });
    if (!res.ok) throw new Error(await res.text());
    await refreshProfiles();
    setStatus(`Saved profile ${profileDraft.name || profile}`);
  }

  async function createProject() {
    if (!projectName.trim()) return;
    const res = await fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: projectName, profile }),
    });
    if (!res.ok) throw new Error(await res.text());
    setProjectName('');
    await refreshProjects();
    setStatus('Created project');
  }

  async function buildVoiceProfile() {
    const res = await fetch('/api/voice-profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: voiceName, samples: voiceSamples }),
    });
    if (!res.ok) throw new Error(await res.text());
    await refreshProfiles();
    setStatus(`Built voice profile ${voiceName}`);
  }

  async function runBatch(batchMode: 'diagnose' | 'rewrite') {
    const next = [];
    for (const item of batchItems) {
      const url = batchMode === 'diagnose' ? '/api/diagnose' : mode === 'deslop' ? '/api/deslop' : '/api/rewrite';
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: item.text, profile, mode, passes, provider, preserve }),
      });
      next.push({ ...item, report: await res.json() });
    }
    setBatchItems(next);
    setStatus(`Processed ${next.length} files`);
  }

  async function analyzeManuscript() {
    if (!manuscriptItems.length) { setStatus('Import manuscript files first.'); return; }
    setStatus('Analyzing manuscript...');
    const res = await fetch('/api/manuscript', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ documents: manuscriptItems, profile, project: manuscriptProject || null }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    setManuscriptReport(data);
    setStatus(data.run_id ? `Analyzed ${data.document_count} files - saved run ${data.run_id}` : `Analyzed ${data.document_count} files`);
  }

  async function calibrateCorpus() {
    if (!calibrationSamples.length) { setStatus('Import model samples first.'); return; }
    setStatus('Calibrating corpus...');
    const res = await fetch('/api/calibrate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: calibrationName, samples: calibrationSamples, reference_samples: calibrationReferences }),
    });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    setCalibrationResult(data);
    setStatus(`Saved calibration ${data.name}`);
  }

  function updateProfileList(key: string, value: string) {
    setProfileDraft({ ...profileDraft, [key]: value.split(/\r?\n|,/).map((x) => x.trim()).filter(Boolean) });
  }

  function updateFilterSetting(key: string, value: any) {
    const current = profileDraft.filter_words || {};
    setProfileDraft({ ...profileDraft, filter_words: { ...current, [key]: value } });
  }

  function updateFilterCategory(category: string, value: string) {
    const current = profileDraft.filter_words || {};
    const categories = current.categories || defaultFilterCategories;
    setProfileDraft({
      ...profileDraft,
      filter_words: {
        ...current,
        categories: {
          ...categories,
          [category]: value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean),
        },
      },
    });
  }

  function diffWords(before: string, after: string) {
    const a = before.split(/(\s+)/);
    const b = after.split(/(\s+)/);
    if (a.length * b.length > 250000) return [{ kind: 'same', text: after }];
    const dp = Array.from({ length: a.length + 1 }, () => Array(b.length + 1).fill(0));
    for (let i = a.length - 1; i >= 0; i--) for (let j = b.length - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    const out: { kind: string; text: string }[] = [];
    let i = 0, j = 0;
    while (i < a.length && j < b.length) {
      if (a[i] === b[j]) { out.push({ kind: 'same', text: a[i++] }); j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) out.push({ kind: 'del', text: a[i++] });
      else out.push({ kind: 'add', text: b[j++] });
    }
    while (i < a.length) out.push({ kind: 'del', text: a[i++] });
    while (j < b.length) out.push({ kind: 'add', text: b[j++] });
    return out;
  }

  const tabs = ['editor', 'diff', 'manuscript', 'calibration', 'settings', 'profiles', 'projects', 'batch', 'voice', 'agents'];

  return <div className="app">
    <header>
      <div className="brand"><strong>ThothPad</strong><small>See what your prose is doing.</small></div>
      {tabs.map((t) => <button key={t} className={tab === t ? 'tab active' : 'tab'} onClick={() => setTab(t)}>{t}</button>)}
      <span>{status}</span>
    </header>

    {tab === 'editor' && <main className="editor-grid">
      <section className="toolbar-panel">
        <div className="toolbar">
          <select value={profile} onChange={(e) => setProfile(e.target.value)}>{profiles.map((p) => <option key={p.name}>{p.name}</option>)}</select>
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="rewrite">Rewrite</option><option value="deslop">Deslop</option><option value="line_edit">Line edit</option><option value="write_from_brief">Write from brief</option>
          </select>
          <input type="number" min={1} max={4} value={passes} onChange={(e) => setPasses(Number(e.target.value))} />
          <button onClick={() => call('/api/diagnose', runBody())}><ScanText size={16} />Analyze</button>
          <button onClick={() => call(mode === 'deslop' ? '/api/deslop' : '/api/rewrite', runBody())}><Sparkles size={16} />Rewrite</button>
          <button className="secondary" onClick={() => call('/api/compare', { before: input, after: output, profile })}><GitCompare size={16} />Compare</button>
          <input ref={fileInputRef} className="file-input" type="file" accept=".txt,.md,.markdown,text/plain,text/markdown" onChange={(e) => importFile(e.target.files?.[0])} />
          <button className="secondary" onClick={() => fileInputRef.current?.click()}><FileUp size={16} />Import</button>
          <button className="secondary" onClick={() => saveText(output || report?.output_text || '', `${sourceName}-rewritten.md`, 'text/markdown')}><FileDown size={16} />Save Output</button>
          <button className="secondary" onClick={() => saveText(report ? JSON.stringify(report, null, 2) : '', `${sourceName}-report.json`, 'application/json')}><FileJson size={16} />Save Report</button>
        </div>
      </section>
      <section><h2>Original</h2><textarea ref={inputRef} value={input} onChange={(e) => setInput(e.target.value)} /></section>
      <section><h2>Rewritten</h2><textarea ref={outputRef} value={output} onChange={(e) => setOutput(e.target.value)} /></section>
      <aside>
        <h2>Locked Facts / Preserve List</h2>
        <textarea className="mini" value={preserveText} onChange={(e) => setPreserveText(e.target.value)} placeholder={'One fact per line:\nCharacter names\nLore terms\nPOV\nSpecific dialogue'} />
        <div className="scores"><b>Before {report?.score_before ?? '-'}</b><b>After {report?.score_after ?? '-'}</b></div>
        {flags.length ? flags.map((flag, i) => <button className="flag" key={i} onClick={() => jumpToFlag(flag)}>
          <b>{flag.severity} - {flag.analyzer} - {flag.type}</b><span>{flag.excerpt}</span><small>Source: {flag.source || 'unspecified'} - {flag.suggestion}</small>
        </button>) : <p className="muted">Run Analyze or Rewrite.</p>}
      </aside>
    </main>}

    {tab === 'diff' && <main className="single"><h2>Before / After Diff</h2><div className="scores"><b>Delta {(report?.score_after ?? 0) - (report?.score_before ?? 0)}</b><b>{report?.score_before ?? '-'} {'->'} {report?.score_after ?? '-'}</b></div><pre className="diff">{diffWords(input, output).map((p, i) => <span key={i} className={p.kind}>{p.text}</span>)}</pre></main>}

    {tab === 'manuscript' && <main className="single manuscript">
      <h2>Manuscript Analysis</h2>
      <div className="toolbar">
        <input ref={manuscriptInputRef} className="file-input" type="file" multiple accept=".txt,.md,.markdown" onChange={(e) => importMany(e.target.files, setManuscriptItems)} />
        <button onClick={() => manuscriptInputRef.current?.click()}><FileUp size={16} />Import Files</button>
        <select value={manuscriptProject} onChange={(e) => setManuscriptProject(e.target.value)}>
          <option value="">No project ledger</option>
          {projects.map((project) => <option key={project.path} value={project.name}>{project.name}</option>)}
        </select>
        <button onClick={analyzeManuscript}><BookOpenCheck size={16} />Analyze Manuscript</button>
        <button className="secondary" onClick={() => saveText(manuscriptReport ? JSON.stringify(manuscriptReport, null, 2) : '', 'thothpad-manuscript-report.json', 'application/json')}><FileJson size={16} />Save Report</button>
        <span className="muted">{manuscriptItems.length} files loaded</span>
      </div>
      {!manuscriptReport ? <p className="empty-state">Import chapter files and run the manuscript analyzer.</p> : <>
        <div className="manuscript-summary">
          <div><small>Documents</small><b>{manuscriptReport.document_count}</b></div>
          <div><small>Words</small><b>{manuscriptReport.manuscript_stats?.word_count}</b></div>
          <div><small>MTLD</small><b>{manuscriptReport.manuscript_stats?.mtld}</b></div>
          <div><small>MATTR</small><b>{manuscriptReport.manuscript_stats?.mattr_500}</b></div>
          <div><small>Score</small><b>{manuscriptReport.score_before}</b></div>
        </div>
        <div className="report-columns">
          <section className="report-section"><h3>Repeated Words</h3>{(manuscriptReport.repetition?.repeated_words || []).slice(0, 40).map((item: any) => <div className="metric-row" key={item.lemma}><b>{item.lemma}</b><span>{item.count} uses</span><small>{item.affected_files} files</small></div>)}</section>
          <section className="report-section"><h3>Repeated Phrases</h3>{(manuscriptReport.repetition?.repeated_phrases || []).slice(0, 40).map((item: any) => <div className="metric-row" key={`${item.phrase}-${item.size}`}><b>{item.phrase}</b><span>{item.count} uses</span><small>{item.affected_files} files</small></div>)}</section>
          <section className="report-section"><h3>Recurring Imagery</h3>{(manuscriptReport.repetition?.image_families || []).map((item: any) => <div className="metric-row" key={item.family}><b>{item.family.replaceAll('_', ' ')}</b><span>{item.count} uses</span><small>{item.affected_files} files</small></div>)}</section>
          <section className="report-section"><h3>Pattern Hotspots</h3>{(manuscriptReport.pattern_hotspots || []).slice(0, 40).map((item: any) => <div className="metric-row" key={`${item.analyzer}-${item.type}`}><b>{item.type.replaceAll('_', ' ')}</b><span>{item.total_matches} flags</span><small>{item.affected_files} files - {item.analyzer}</small></div>)}</section>
        </div>
        <h2>Chapter Ledger</h2>
        <div className="chapter-table">{(manuscriptReport.chapters || []).map((chapter: any) => <div className="chapter-row" key={chapter.name}><b>{chapter.name}</b><span>{chapter.word_count} words</span><span>score {chapter.score}</span><span>{chapter.flags.length} flags</span></div>)}</div>
      </>}
    </main>}

    {tab === 'calibration' && <main className="single manuscript">
      <h2>Corpus Calibration</h2>
      <div className="toolbar">
        <input className="name-input" value={calibrationName} onChange={(e) => setCalibrationName(e.target.value)} />
        <input ref={calibrationInputRef} className="file-input" type="file" multiple accept=".txt,.md,.markdown" onChange={(e) => importMany(e.target.files, (rows) => setCalibrationSamples(rows.map((row: any) => row.text)))} />
        <input ref={calibrationReferenceRef} className="file-input" type="file" multiple accept=".txt,.md,.markdown" onChange={(e) => importMany(e.target.files, (rows) => setCalibrationReferences(rows.map((row: any) => row.text)))} />
        <button onClick={() => calibrationInputRef.current?.click()}><FileUp size={16} />Model Samples</button>
        <button className="secondary" onClick={() => calibrationReferenceRef.current?.click()}><FileUp size={16} />Human References</button>
        <button onClick={calibrateCorpus}><ScanText size={16} />Build Calibration</button>
      </div>
      <p className="calibration-counts">{calibrationSamples.length} model samples, {calibrationReferences.length} reference samples</p>
      {!calibrationResult ? <p className="empty-state">Build a provider, model, or genre-specific overrepresentation profile.</p> : <>
        <div className="manuscript-summary">
          <div><small>Samples</small><b>{calibrationResult.sample_count}</b></div>
          <div><small>References</small><b>{calibrationResult.reference_sample_count}</b></div>
          <div><small>Model words</small><b>{calibrationResult.sample_word_count}</b></div>
          <div><small>Reference words</small><b>{calibrationResult.reference_word_count}</b></div>
        </div>
        <div className="report-columns">
          <section className="report-section"><h3>Overrepresented Words</h3>{(calibrationResult.top_overrepresented_words || []).slice(0, 80).map((item: any) => <div className="metric-row" key={item.word}><b>{item.word}</b><span>{item.count} uses</span><small>{item.overrepresentation_ratio}x</small></div>)}</section>
          <section className="report-section"><h3>Overrepresented Phrases</h3>{Object.entries(calibrationResult.top_ngrams || {}).flatMap(([size, rows]: any) => rows.slice(0, 30).map((item: any) => <div className="metric-row" key={`${size}-${item.phrase}`}><b>{item.phrase}</b><span>{item.count} uses</span><small>{item.overrepresentation_ratio}x</small></div>))}</section>
        </div>
        <p className="saved-path">Saved to {calibrationResult.path}. Set a profile's <code>calibration_profile</code> to <code>{calibrationResult.name}</code> to activate it.</p>
      </>}
    </main>}

    {tab === 'settings' && <main className="single"><h2>Model Settings</h2><div className="form-grid">
      {(['provider', 'base_url', 'model'] as const).map((k) => <label key={k}>{k}<input value={provider[k]} onChange={(e) => setProvider({ ...provider, [k]: e.target.value })} /></label>)}
      <label>api_key<input type="password" autoComplete="off" value={provider.api_key} onChange={(e) => setProvider({ ...provider, api_key: e.target.value })} /><small>Kept only for this browser session.</small></label>
      <label>temperature<input type="number" step="0.1" value={provider.temperature} onChange={(e) => setProvider({ ...provider, temperature: Number(e.target.value) })} /></label>
      <label>max_tokens<input type="number" value={provider.max_tokens} onChange={(e) => setProvider({ ...provider, max_tokens: Number(e.target.value) })} /></label>
    </div><h2>Optional Integrations</h2><div className="integration-grid">{Object.entries(integrations).map(([name, value]: any) => <div className="integration-row" key={name}><b>{name}</b><span className={value.available ? 'available' : 'missing'}>{value.available ? 'available' : 'not installed'}</span><small>{value.purpose}</small></div>)}</div></main>}

    {tab === 'profiles' && <main className="single"><h2>Profile Editor</h2>{profileDraft && <>
      <div className="form-grid wide">
        <label>name<input value={profileDraft.name || ''} onChange={(e) => setProfileDraft({ ...profileDraft, name: e.target.value })} /></label>
        <label>register_target<input value={profileDraft.register_target || ''} onChange={(e) => setProfileDraft({ ...profileDraft, register_target: e.target.value })} /></label>
        <label>calibration_profile<input value={profileDraft.calibration_profile || ''} onChange={(e) => setProfileDraft({ ...profileDraft, calibration_profile: e.target.value })} /></label>
        {['prefer', 'avoid', 'hard_bans', 'soft_flags', 'preserve'].map((k) => <label key={k}>{k}<textarea className="mini" value={(profileDraft[k] || []).join('\n')} onChange={(e) => updateProfileList(k, e.target.value)} /></label>)}
        <label>analyzer_weights JSON<textarea className="mini" value={JSON.stringify(profileDraft.analyzer_weights || {}, null, 2)} onChange={(e) => { try { setProfileDraft({ ...profileDraft, analyzer_weights: JSON.parse(e.target.value) }); } catch { } }} /></label>
        <label>cliche_categories JSON<textarea className="mini" value={JSON.stringify(profileDraft.cliche_categories || {}, null, 2)} onChange={(e) => { try { setProfileDraft({ ...profileDraft, cliche_categories: JSON.parse(e.target.value) }); } catch { } }} /></label>
        <label>external_tools JSON<textarea className="mini" value={JSON.stringify(profileDraft.external_tools || {}, null, 2)} onChange={(e) => { try { setProfileDraft({ ...profileDraft, external_tools: JSON.parse(e.target.value) }); } catch { } }} /></label>
      </div>
      <h2>Filter Words</h2>
      <div className="filter-controls">
        <label className="toggle-row"><input type="checkbox" checked={profileDraft.filter_words?.enabled !== false} onChange={(e) => updateFilterSetting('enabled', e.target.checked)} />Enable filter-word rules</label>
        <label className="toggle-row"><input type="checkbox" checked={profileDraft.filter_words?.ignore_dialogue !== false} onChange={(e) => updateFilterSetting('ignore_dialogue', e.target.checked)} />Ignore dialogue</label>
        <label>Severity<select value={profileDraft.filter_words?.severity || 'hard_fail'} onChange={(e) => updateFilterSetting('severity', e.target.value)}><option value="hard_fail">Hard fail</option><option value="strong_flag">Strong flag</option><option value="context_flag">Context flag</option><option value="taste_flag">Taste flag</option></select></label>
      </div>
      <div className="form-grid wide filter-grid">
        {Object.entries(profileDraft.filter_words?.categories || defaultFilterCategories).map(([category, words]: any) => <label key={category}>{category.replaceAll('_', ' ')}<textarea className="mini" value={(words || []).join('\n')} onChange={(e) => updateFilterCategory(category, e.target.value)} /></label>)}
        <label>custom no-nos<textarea className="mini" value={(profileDraft.filter_words?.custom || []).join('\n')} onChange={(e) => updateFilterSetting('custom', e.target.value.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean))} /></label>
      </div>
      <div className="profile-actions"><button onClick={saveProfile}><Save size={16} />Save Profile</button></div>
    </>}</main>}

    {tab === 'projects' && <main className="single"><h2>Project Mode</h2><div className="toolbar"><input className="name-input" placeholder="Novel A, Essays, Mal Wiki Prose" value={projectName} onChange={(e) => setProjectName(e.target.value)} /><button onClick={createProject}>Create Project</button></div>{projects.map((p) => <div className="card" key={p.path}><b>{p.name}</b><p>{p.path}</p><small>Profile: {p.profile}</small></div>)}</main>}

    {tab === 'batch' && <main className="single"><h2>Batch Rewrite</h2><div className="toolbar"><input ref={batchInputRef} className="file-input" type="file" multiple accept=".txt,.md,.markdown" onChange={(e) => importMany(e.target.files, setBatchItems)} /><button onClick={() => batchInputRef.current?.click()}>Import Files</button><button onClick={() => runBatch('diagnose')}>Batch Diagnose</button><button onClick={() => runBatch('rewrite')}>Batch Rewrite</button><button className="secondary" onClick={() => saveText(JSON.stringify(batchItems, null, 2), 'thothpad-batch-results.json', 'application/json')}>Save Batch Results</button></div>{batchItems.map((item) => <div className="card" key={item.name}><b>{item.name}</b><p>{item.report ? `Score ${item.report.score_before} -> ${item.report.score_after}` : `${item.text.length} chars`}</p></div>)}</main>}

    {tab === 'voice' && <main className="single"><h2>Voice Sample Import</h2><div className="toolbar"><input className="name-input" value={voiceName} onChange={(e) => setVoiceName(e.target.value)} /><input ref={voiceInputRef} className="file-input" type="file" multiple accept=".txt,.md,.markdown" onChange={(e) => importMany(e.target.files, (rows) => setVoiceSamples(rows.map((r: any) => r.text)))} /><button onClick={() => voiceInputRef.current?.click()}>Import Samples</button><button onClick={buildVoiceProfile}>Build Voice Profile</button></div><p>{voiceSamples.length} samples loaded.</p></main>}

    {tab === 'agents' && <main className="single"><h2>Agent Setup</h2><pre className="code">{JSON.stringify(agentSetup, null, 2)}</pre></main>}
  </div>;
}

createRoot(document.getElementById('root')!).render(<App />);
