// Runs the Crypto Quant Desk engine (pure Python) in Pyodide, off the UI thread.
// It receives Kraken public JSON from the page and returns JSON. No network
// access of its own except loading the runtime and the engine bundle.
const PYODIDE_VERSION = "314.0.7";
const INDEX_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;
importScripts(INDEX_URL + "pyodide.js");

let demo = null;
const booting = (async () => {
  const t0 = performance.now();
  postMessage({ type: "progress", text: "Loading Python runtime…" });
  const py = await loadPyodide({ indexURL: INDEX_URL });
  postMessage({ type: "progress", text: "Loading numpy, pandas, pydantic…" });
  await py.loadPackage(["numpy", "pandas", "pydantic"]);
  postMessage({ type: "progress", text: "Loading the engine…" });
  const res = await fetch("cqd-engine.zip");
  if (!res.ok) throw new Error("engine bundle missing (" + res.status + ")");
  py.unpackArchive(await res.arrayBuffer(), "zip");
  demo = py.pyimport("cqd.web.demo");
  postMessage({ type: "ready", ms: performance.now() - t0 });
})().catch((e) => postMessage({ type: "fatal", error: String(e) }));

onmessage = async (ev) => {
  const { id, fn, args } = ev.data;
  try {
    await booting;
    if (!demo) throw new Error("engine failed to load");
    const t0 = performance.now();
    let out;
    if (fn === "portfolio") out = demo.analyze_portfolio_json(args.ohlc, args.weights, args.nav);
    else if (fn === "strategy") out = demo.analyze_strategy_json(args.btc, args.pairs, args.equity);
    else throw new Error("unknown call " + fn);
    postMessage({ type: "result", id, json: out, ms: performance.now() - t0 });
  } catch (e) {
    postMessage({ type: "result", id, error: String(e.message || e) });
  }
};
