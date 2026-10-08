import { useEffect, useRef, useState } from "react";
import { ASSIST_LENGTH_LABELS, ASSIST_LIMITS, QUICK_EDITS, requestScriptAssist } from "../lib/scriptAssist";
import type { AssistAction, AssistLength, AssistResult, ScriptAssistContext } from "../lib/scriptAssist";

const message = (error: unknown) => error instanceof Error && error.message ? error.message : "AI 写作没有成功，稿子没有被改动。请稍后重试。";

/**
 * Write a first draft from a few facts, or polish/rewrite the draft already on the page.
 * The result is shown for review and only replaces the manuscript when the user adopts it; adopting can be undone.
 */
export default function ScriptAssistant({ mode, script, disabled, maxScript, onApply }: ScriptAssistContext) {
  const [brief, setBrief] = useState("");
  const [length, setLength] = useState<AssistLength>("normal");
  const [instruction, setInstruction] = useState("");
  const [busy, setBusy] = useState<AssistAction | null>(null);
  const [result, setResult] = useState<(AssistResult & { action: AssistAction; editInstruction: string }) | null>(null);
  const [error, setError] = useState("");
  const [adopted, setAdopted] = useState<{ previous: string; text: string } | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  // A draft written for one production mode must never be adopted into another: drop everything on a mode change.
  useEffect(() => {
    controller.current?.abort(); controller.current = null;
    setBusy(null); setResult(null); setError(""); setAdopted(null);
  }, [mode]);

  if (mode === "original") {
    return <aside className="gm-assist" data-testid="script-assist-unavailable" aria-label="AI 写作">
      <p className="gm-assist-title"><b>AI 写作</b></p>
      <p className="gm-muted gm-small">“只用原声”的稿子必须是素材里真实说过的话：先上传采访素材，转写后从里面挑句子。这个模式不能由 AI 写或改，避免编造原话。</p>
    </aside>;
  }
  const hasScript = script.trim().length > 0;
  const mixed = mode === "mixed";

  async function run(action: AssistAction, editInstruction = "") {
    if (busy || disabled) return;
    controller.current?.abort();
    const abort = new AbortController(); controller.current = abort;
    setBusy(action); setError(""); setResult(null);
    try {
      const value = await requestScriptAssist(action === "write"
        ? { action, mode: mode as "voiceover" | "mixed", brief, instruction: instruction.trim(), length }
        : { action, mode: mode as "voiceover" | "mixed", text: script, instruction: editInstruction.trim() }, abort.signal);
      if (controller.current === abort && !abort.signal.aborted) setResult({ ...value, action, editInstruction });
    } catch (failure) {
      if (!abort.signal.aborted) setError(message(failure));
    } finally {
      if (controller.current === abort) { setBusy(null); controller.current = null; }
    }
  }
  function adopt() {
    if (!result) return;
    setAdopted({ previous: script, text: result.text });
    onApply(result.text); setResult(null);
  }
  function undo() {
    if (!adopted) return;
    onApply(adopted.previous); setAdopted(null);
  }
  function cancel() { controller.current?.abort(); controller.current = null; setBusy(null); }

  const canUndo = adopted !== null && script === adopted.text;
  const tooLong = result !== null && result.text.length > maxScript;
  return <aside className="gm-assist" data-testid="script-assist" aria-label="AI 写作助手">
    <p className="gm-assist-title"><b>AI 写作助手</b><span className="gm-muted gm-small"> · 会调用大模型，可能产生少量费用；写出的稿子需要你核对事实</span></p>
    <div className="gm-assist-block">
      <label htmlFor="gm-assist-brief"><b>还没有稿子？</b>写几句要点，AI 帮你写一稿</label>
      <textarea id="gm-assist-brief" className="gm-assist-brief" value={brief} maxLength={ASSIST_LIMITS.brief} rows={3} disabled={disabled || busy !== null}
        placeholder="例如：3月5日，南宁信息港广场举办迎春市集，有120家商户参加，市民可以买年货、看表演。"
        onChange={event => setBrief(event.target.value)} />
      <div className="gm-row gm-assist-row">
        <span className="gm-small" role="group" aria-label="篇幅">篇幅：{(Object.keys(ASSIST_LENGTH_LABELS) as AssistLength[]).map(key =>
          <label key={key} className="gm-assist-radio"><input type="radio" name="gm-assist-length" checked={length === key} disabled={disabled || busy !== null}
            onChange={() => setLength(key)} /> {ASSIST_LENGTH_LABELS[key]}</label>)}</span>
        <button type="button" className="gm-small-button gm-assist-go" data-control="assist-write" disabled={disabled || busy !== null || !brief.trim()} onClick={() => void run("write")}>
          {busy === "write" ? "AI 正在写…" : "AI 写一稿"}</button>
      </div>
    </div>
    <div className="gm-assist-block">
      <p className="gm-assist-label"><b>已有稿子？</b>让 AI 帮你改{mixed ? "（“同期”原声句会原样保留）" : ""}</p>
      <div className="gm-row gm-assist-chips" role="group" aria-label="快捷修改">{QUICK_EDITS.map(item =>
        <button key={item.label} type="button" className="gm-small-button" data-control="assist-quick" disabled={disabled || busy !== null || !hasScript}
          onClick={() => void run("edit", item.instruction)}>{item.label}</button>)}</div>
      <div className="gm-row gm-assist-row">
        <input className="gm-assist-instruction" aria-label="自定义修改要求" value={instruction} maxLength={ASSIST_LIMITS.instruction} disabled={disabled || busy !== null}
          placeholder="或者自己说要求，例如：缩短到 200 字，开头先说结果" onChange={event => setInstruction(event.target.value)} />
        <button type="button" className="gm-small-button gm-assist-go" data-control="assist-edit" disabled={disabled || busy !== null || !hasScript || !instruction.trim()}
          onClick={() => void run("edit", instruction)}>{busy === "edit" ? "AI 正在改…" : "按要求修改"}</button>
      </div>
      {!hasScript && <p className="gm-muted gm-small">先在上面的稿子框里写或粘贴内容，才能让 AI 修改。</p>}
    </div>
    {busy && <p role="status" className="gm-assist-busy">AI {busy === "write" ? "正在写稿" : "正在修改"}，通常十几秒到一两分钟。<button type="button" className="gm-small-button" data-control="assist-cancel" onClick={cancel}>取消</button></p>}
    {error && <p className="gm-danger-text gm-small" role="alert" data-testid="script-assist-error">{error}稿子没有被改动。</p>}
    {result && <section className="gm-assist-result" data-testid="script-assist-result" aria-label="AI 给出的稿子">
      <p><b>{result.action === "write" ? "AI 写好了" : "AI 改好了"}</b><span className="gm-muted gm-small"> · 共 {result.text.length} 字 · 还没有替换你的稿子，看过再决定</span></p>
      <pre className="gm-assist-preview" tabIndex={0}>{result.text}</pre>
      {result.warnings.length > 0 && <ul className="gm-assist-warnings" role="alert">{result.warnings.map(item => <li key={item}>{item}</li>)}</ul>}
      {tooLong && <p className="gm-danger-text gm-small">这一稿超过 {maxScript} 字上限，采用后需要再精简。</p>}
      <p className="gm-muted gm-small">AI 可能写错人名、数字、日期和因果，请逐项核对；这不是事实认证。{hasScript ? "采用后会替换上面的稿子，可以撤销。" : ""}</p>
      <div className="gm-row">
        <button type="button" className="gm-small-button gm-assist-go" data-control="assist-adopt" onClick={adopt}>采用这一稿</button>
        <button type="button" className="gm-small-button" data-control="assist-retry" disabled={busy !== null || disabled}
          onClick={() => void run(result.action, result.editInstruction)}>再来一稿</button>
        <button type="button" className="gm-small-button" data-control="assist-discard" onClick={() => setResult(null)}>放弃</button>
      </div>
    </section>}
    {canUndo && !result && <p className="gm-assist-undo" role="status">已采用 AI 的稿子。<button type="button" className="gm-small-button" data-control="assist-undo" onClick={undo}>撤销，恢复原来的稿子</button></p>}
  </aside>;
}
