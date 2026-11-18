# Phase 1 — visual chart input (YOLOv8-compatible)

This isolated optional package adds bounded image decoding, lazy YOLO inference,
explicit screenshot-to-candle coordinate alignment, and sparse research-candidate
scores. No model weights are included; do not load untrusted weight files.

## Install/configure

```sh
python -m pip install -r requirements.txt
python -m pip install -r requirements-vision.txt
```

Set `CHARTANALYZER_YOLO_MODEL` to a trusted local YOLOv8-compatible `.pt` file
trained with the intended class names. Inference output records the file name and
SHA-256. Core app dependencies remain unchanged.

## API integration point

`chart_vision.analyze_chart_upload(payload, ohlcv_loader=..., timeframe_seconds=...)`
is framework-independent. In `app.py`, register a JSON `POST` route that calls it
with the application's `load_cached` function and `TF_MIN[tf] * 60`; enforce the
existing request-size and authorization protections. Required JSON keys are
`image_base64`, `source`, `symbol`, and `tf`. The detector uses no user-supplied
model path; configure the local trusted model via `CHARTANALYZER_YOLO_MODEL`.

When `chart_mapping` is supplied, it must include `plot_bounds` (pixel rectangle),
`start_timestamp`, `end_timestamp` (Unix seconds), and linear `price_min` /
`price_max`. The current mapper intentionally rejects log-price axes. Without a
calibration the result is a visual candidate, not an OHLCV signal. The pipeline
fetches corresponding OHLCV through the injected existing loader and audits it
with `data_integrity.audit_ohlcv` before any mapping. Invalid OHLCV blocks mapping.

The returned sparse signal is a research input only. Model confidence is not used
as a probability, no exits or trade permissions are inferred, and this utility does
not itself run bootstrap/FDR/OOS. Define and pre-register the exact frozen rule,
then pass its OHLCV-aligned signal into existing backtest/Walk-Forward research,
block bootstrap, `ResearchRegistry.apply_fdr`, and the deployment gate. A mapped
bbox is only coordinate alignment; it does not prove that the underlying candles
exhibit the named morphology or that it predicts returns. Accuracy is dataset and
chart-style specific; evaluate on held-out screenshots rather than assuming a
published accuracy transfers.

## Existing architecture touchpoints

- `app.py`: route integration via `load_cached`, timeframe validation and API
  request limits. Existing dashboard is a custom HTTP/HTML app; this phase does
  not replace it with Streamlit/Next.js.
- `data_integrity.py`: mandatory OHLCV audit is called by the pipeline.
- `statistical_edge.py`, `walk_forward.py`, `research_statistics.py`, and
  `research_registry.py`: remain the statistical authority; detection never changes
  existing signal formulas or deployment eligibility.
- `requirements-vision.txt`: optional heavyweight inference dependencies only.

Live feeds remain out of scope here and must remain in a separate source/buffer from
historical research until explicitly snapshotted, fingerprinted and validated.
