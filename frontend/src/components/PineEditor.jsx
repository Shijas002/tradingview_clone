// Pine Script editor (CodeMirror 6): write, save, compile and add scripts to the chart.
import React, { useCallback, useEffect, useRef, useState } from "react";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import { autocompletion, closeBrackets, closeBracketsKeymap, completionKeymap } from "@codemirror/autocomplete";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { bracketMatching, indentOnInput, indentUnit } from "@codemirror/language";
import { lintGutter, setDiagnostics } from "@codemirror/lint";
import { pineCompletions, pineHighlight, pineLanguage } from "../editor/pineLanguage.js";
import { BUILTIN_INDICATORS, NEW_INDICATOR_TEMPLATE, NEW_STRATEGY_TEMPLATE } from "../pine/builtins.js";
import { errorLocation, pine } from "../pine/pineClient.js";
import { useStore } from "../store/useStore.js";
import { Dropdown, MenuItem } from "./ui.jsx";

const editorTheme = EditorView.theme(
  {
    "&": { height: "100%", fontSize: "13px", backgroundColor: "#1E1E1E", color: "#D4D4D4" },
    ".cm-content": { fontFamily: "Menlo, Consolas, 'Courier New', monospace", caretColor: "#AEAFAD" },
    ".cm-gutters": { backgroundColor: "#1E1E1E", color: "#858585", border: "none" },
    ".cm-activeLine": { backgroundColor: "#2A2D2E" },
    ".cm-activeLineGutter": { backgroundColor: "#2A2D2E" },
    "&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection": { backgroundColor: "#264F78 !important" },
    ".cm-tooltip": { backgroundColor: "#252526", border: "1px solid #454545", color: "#D4D4D4" },
    ".cm-tooltip-autocomplete > ul > li[aria-selected]": { backgroundColor: "#04395E" },
    ".cm-cursor": { borderLeftColor: "#AEAFAD" },
  },
  { dark: true },
);

function scriptTitle(source) {
  const m = /(indicator|strategy|library)\s*\(\s*["']([^"']+)["']/.exec(source || "");
  return m ? m[2] : "My script";
}

export default function PineEditor() {
  const hostRef = useRef(null);
  const viewRef = useRef(null);
  const editor = useStore((s) => s.editor);
  const scripts = useStore((s) => s.scripts);
  const [name, setName] = useState(editor.name || scriptTitle(editor.source));
  const [log, setLog] = useState([]);
  const [busy, setBusy] = useState(false);
  const nameTouched = useRef(false); // once the user edits the name we stop deriving it from indicator("...")
  const saveTimer = useRef(null);
  const loadedKey = useRef(null);

  const addLog = useCallback((text, kind = "info") => {
    setLog((l) => [...l.slice(-60), { t: new Date().toLocaleTimeString(), text, kind }]);
  }, []);

  const getSource = () => (viewRef.current ? viewRef.current.state.doc.toString() : editor.source || "");

  const setSource = (src) => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: src } });
    view.dispatch(setDiagnostics(view.state, []));
  };

  // ---- create editor once
  useEffect(() => {
    const initial = useStore.getState().editor.source || NEW_INDICATOR_TEMPLATE;
    const state = EditorState.create({
      doc: initial,
      extensions: [
        lineNumbers(),
        highlightActiveLineGutter(),
        history(),
        drawSelection(),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        highlightActiveLine(),
        highlightSelectionMatches(),
        indentUnit.of("    "),
        autocompletion({ override: [pineCompletions] }),
        lintGutter(),
        pineLanguage,
        pineHighlight,
        editorTheme,
        keymap.of([
          { key: "Mod-s", preventDefault: true, run: () => (document.getElementById("pine-save")?.click(), true) },
          { key: "Mod-Enter", preventDefault: true, run: () => (document.getElementById("pine-add")?.click(), true) },
          indentWithTab,
          ...closeBracketsKeymap,
          ...defaultKeymap,
          ...searchKeymap,
          ...historyKeymap,
          ...completionKeymap,
        ]),
        EditorView.updateListener.of((u) => {
          if (!u.docChanged) return;
          clearTimeout(saveTimer.current);
          saveTimer.current = setTimeout(() => useStore.getState().setEditor({ source: u.state.doc.toString(), dirty: true }), 400);
        }),
      ],
    });
    const view = new EditorView({ state, parent: hostRef.current });
    viewRef.current = view;
    hostRef.current.cmView = view; // handle for automated UI tests
    if (!useStore.getState().editor.source) useStore.getState().setEditor({ source: initial });
    return () => {
      clearTimeout(saveTimer.current);
      view.destroy();
      viewRef.current = null;
    };
  }, []);

  // ---- external "open in editor" requests (legend </> button) replace the document
  useEffect(() => {
    const key = `${editor.scriptId || ""}|${editor.openNonce || ""}`;
    const view = viewRef.current;
    if (!view || editor.source === undefined || editor.source === null) return;
    if (loadedKey.current === null) {
      loadedKey.current = key;
      return;
    }
    if (key !== loadedKey.current && editor.source !== view.state.doc.toString()) {
      loadedKey.current = key;
      setSource(editor.source);
      setName(editor.name || scriptTitle(editor.source));
    }
  }, [editor.scriptId, editor.openNonce, editor.source, editor.name]);

  // Report runtime results of this script on the active chart (errors only show up when it runs on data)
  const runInfo = useStore((st) => {
    const pane = st.panes.find((p) => p.id === st.activePaneId) || st.panes[0];
    const ind = pane && editor.scriptId ? pane.indicators.find((i) => i.scriptId === editor.scriptId) : null;
    const info = ind ? (st.paneRuntime[pane.id]?.indicators || []).find((i) => i.id === ind.id) : null;
    return info && !info.running ? `${info.error || ""}|${info.elapsedMs ?? ""}` : null;
  });
  const lastError = useRef(null);
  const awaitingResult = useRef(false); // log the first successful calculation after a save
  useEffect(() => {
    if (!runInfo) return;
    const [err, ms] = runInfo.split("|");
    if (err && err !== lastError.current) addLog(`Runtime error: ${err}`, "error");
    else if (!err && ms && (awaitingResult.current || lastError.current)) addLog(`Calculated on chart in ${ms} ms`, "ok");
    if (!err) awaitingResult.current = false;
    lastError.current = err || null;
  }, [runInfo, addLog]);

  const s = useStore.getState();

  const compile = async (src) => {
    const view = viewRef.current;
    try {
      const info = await pine.inspect(src);
      if (view) view.dispatch(setDiagnostics(view.state, []));
      return { ok: true, info };
    } catch (err) {
      const msg = err.message || String(err);
      const loc = errorLocation(msg);
      if (view && loc) {
        const line = Math.min(Math.max(1, loc.line), view.state.doc.lines);
        const l = view.state.doc.line(line);
        const from = Math.min(l.to, l.from + Math.max(0, loc.col - 1));
        view.dispatch(setDiagnostics(view.state, [{ from, to: Math.max(from + 1, l.to), severity: "error", message: msg }]));
      }
      addLog(msg, "error");
      return { ok: false, error: msg };
    }
  };

  const save = async () => {
    clearTimeout(saveTimer.current); // a pending "dirty" update must not land after the save
    const src = getSource();
    const derived = scriptTitle(src);
    const nm = ((nameTouched.current ? name : derived) || derived).trim() || "My script";
    if (nm !== name) setName(nm);
    const id = s.saveScript({ id: useStore.getState().editor.scriptId, name: nm, source: src });
    s.setEditor({ scriptId: id, source: src, name: nm, dirty: false });
    loadedKey.current = `${id}|${useStore.getState().editor.openNonce || ""}`;
    const res = await compile(src);
    awaitingResult.current = res.ok;
    addLog(res.ok ? `Saved "${nm}" — compiled without errors` : `Saved "${nm}" with errors`, res.ok ? "ok" : "error");
    return { id, nm, src, ok: res.ok };
  };

  const addToChart = async () => {
    setBusy(true);
    try {
      const { id, nm, src, ok } = await save();
      if (!ok) return;
      const st = useStore.getState();
      const pane = st.activePane();
      const already = pane.indicators.some((i) => i.scriptId === id);
      if (already) addLog(`Updated "${nm}" on the chart`, "ok");
      else {
        st.addIndicator(pane.id, { name: nm, source: src, scriptId: id });
        addLog(`Added "${nm}" to ${pane.symbol}`, "ok");
      }
    } finally {
      setBusy(false);
    }
  };

  const newScript = (template) => {
    if (useStore.getState().editor.dirty && !window.confirm("Discard unsaved changes?")) return;
    setSource(template);
    const nm = scriptTitle(template);
    nameTouched.current = false;
    setName(nm);
    s.setEditor({ scriptId: null, source: template, name: nm, dirty: false });
    loadedKey.current = `|${useStore.getState().editor.openNonce || ""}`;
    setLog([]);
  };

  const openScript = (sc) => {
    if (useStore.getState().editor.dirty && !window.confirm("Discard unsaved changes?")) return;
    setSource(sc.source);
    nameTouched.current = sc.name !== scriptTitle(sc.source);
    setName(sc.name);
    s.setEditor({ scriptId: sc.id, source: sc.source, name: sc.name, dirty: false });
    loadedKey.current = `${sc.id}|${useStore.getState().editor.openNonce || ""}`;
  };

  const openBuiltin = (b) => {
    if (useStore.getState().editor.dirty && !window.confirm("Discard unsaved changes?")) return;
    const src = b.source.replace(/(indicator|strategy)\(\s*"([^"]+)"/, (_m, kind, title) => `${kind}("${title} (copy)"`);
    setSource(src);
    nameTouched.current = false;
    setName(`${b.name} (copy)`);
    s.setEditor({ scriptId: null, source: src, name: `${b.name} (copy)`, dirty: true });
  };

  const del = () => {
    const id = useStore.getState().editor.scriptId;
    if (!id) return;
    if (!window.confirm(`Delete script "${name}"? Charts using it keep their current copy.`)) return;
    s.deleteScript(id);
    s.setEditor({ scriptId: null, dirty: true });
    addLog(`Deleted "${name}"`);
  };

  return (
    <div className="pine-editor">
      <div className="pine-toolbar">
        <input className="pine-name" value={name} onChange={(e) => {
            nameTouched.current = true;
            setName(e.target.value);
          }} title="Script name" spellCheck={false} />
        {editor.dirty && <span className="pine-dirty" title="Unsaved changes">●</span>}
        <Dropdown label="New" buttonClass="btn small">
          <MenuItem onClick={() => newScript(NEW_INDICATOR_TEMPLATE)}>Indicator</MenuItem>
          <MenuItem onClick={() => newScript(NEW_STRATEGY_TEMPLATE)}>Strategy</MenuItem>
        </Dropdown>
        <Dropdown label="Open" buttonClass="btn small" className="pine-open">
          <div className="menu-section">My scripts</div>
          {!scripts.length && <div className="menu-empty">No saved scripts</div>}
          {scripts.map((sc) => (
            <MenuItem key={sc.id} onClick={() => openScript(sc)} active={sc.id === editor.scriptId}>
              {sc.name}
            </MenuItem>
          ))}
          <div className="menu-section">Built-in (opens a copy)</div>
          {BUILTIN_INDICATORS.map((b) => (
            <MenuItem key={b.id} onClick={() => openBuiltin(b)}>
              {b.name}
            </MenuItem>
          ))}
        </Dropdown>
        <span className="flex1" />
        <button className="btn small" id="pine-save" onClick={save} title="Save (Ctrl+S)">
          Save
        </button>
        {editor.scriptId && (
          <button className="btn small danger" onClick={del} title="Delete script">
            Delete
          </button>
        )}
        <button className="btn small primary" id="pine-add" onClick={addToChart} disabled={busy} title="Save and add to chart (Ctrl+Enter)">
          {busy ? "Compiling…" : "Add to chart"}
        </button>
      </div>
      <div className="pine-body">
        <div className="pine-code" ref={hostRef} />
        <div className="pine-console">
          {!log.length && <div className="muted">Console — compile errors and messages appear here. Pine Script v5/v6 runs locally via the PineTS engine.</div>}
          {log.map((l, i) => (
            <div key={i} className={`log-${l.kind}`}>
              <span className="log-time">{l.t}</span> {l.text}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
