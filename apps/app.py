"""Streamlit review UI for water-meter OCR."""

from __future__ import annotations

import csv
import hashlib
import io

import streamlit as st

from meter_reader import extract
from schema import combine_reading, reading_text

st.set_page_config(page_title="Water Meter OCR", page_icon="💧", layout="wide")
st.title("💧 Water Meter OCR")
st.caption(
    "Upload meter photos, analyze them with Gemini on Vertex AI, review uncertain "
    "values, and export the checked results."
)

MIME_BY_EXT = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}

if "ocr_results" not in st.session_state:
    st.session_state.ocr_results = {}

uploaded = st.file_uploader(
    "Meter photos",
    type=list(MIME_BY_EXT),
    accept_multiple_files=True,
    help="Use clear, close photos with the complete digit strip in frame.",
)

if not uploaded:
    st.info("Add one or more PNG or JPEG meter photos to begin.")
    st.stop()

files = []
for file in uploaded:
    image_bytes = file.getvalue()
    digest = hashlib.sha256(image_bytes).hexdigest()
    ext = file.name.rsplit(".", 1)[-1].lower()
    files.append((file, image_bytes, digest, MIME_BY_EXT.get(ext, "image/png")))

pending_count = sum(
    1 for _, _, digest, _ in files if not st.session_state.ocr_results.get(digest, {}).get("ok")
)
action_col, clear_col, status_col = st.columns([1, 1, 3])
analyze = action_col.button(
    "Analyze photos" if pending_count else "Re-analyze photos",
    type="primary",
    use_container_width=True,
)
if clear_col.button("Clear cached results", use_container_width=True):
    st.session_state.ocr_results = {}
    st.rerun()
status_col.caption(
    f"{len(files)} photo(s) selected · {pending_count} awaiting successful analysis"
)

if analyze:
    progress = st.progress(0, text="Starting analysis…")
    for index, (file, image_bytes, digest, mime) in enumerate(files, start=1):
        progress.progress((index - 1) / len(files), text=f"Reading {file.name}…")
        try:
            result = extract(image_bytes, mime)
            st.session_state.ocr_results[digest] = {
                "ok": True,
                "file_name": file.name,
                "result": result.model_dump(mode="json"),
            }
        except Exception as exc:  # noqa: BLE001
            st.session_state.ocr_results[digest] = {
                "ok": False,
                "file_name": file.name,
                "error": str(exc),
            }
    progress.progress(1.0, text="Analysis complete")

rows: list[dict[str, object]] = []
for file, image_bytes, digest, _ in files:
    record = st.session_state.ocr_results.get(digest)
    st.divider()
    col_img, col_res = st.columns([1, 1])
    with col_img:
        st.image(image_bytes, caption=file.name, use_container_width=True)
    with col_res:
        if record is None:
            st.info("Select **Analyze photos** to read this meter.")
            continue
        if not record["ok"]:
            st.error(f"Analysis failed: {record['error']}")
            continue

        result = record["result"]
        reading = result.get("reading_m3")
        st.metric("Meter reading", f"{reading} m³" if reading is not None else "Unreadable")
        c1, c2, c3 = st.columns(3)
        c1.metric("Whole (black)", result["integer_part"] or "—")
        c2.metric("Decimal (red)", result["decimal_part"] or "—")
        c3.metric("Confidence", f"{float(result['confidence']):.0%}")
        st.metric("Handwritten label", result["handwritten_label"] or "—")
        if result["review_required"]:
            st.warning("Manual review required before using this result.")
        if result["notes"]:
            st.caption(f"📝 {result['notes']}")

        rows.append(
            {
                "file": file.name,
                "integer_part": result["integer_part"],
                "decimal_part": result["decimal_part"],
                "reading_m3": reading or "",
                "handwritten_label": result["handwritten_label"],
                "confidence": float(result["confidence"]),
                "review_required": bool(result["review_required"]),
                "notes": result["notes"],
            }
        )

if rows:
    st.divider()
    st.subheader("Review and export")
    st.caption(
        "Correct the digit fields or handwritten label when needed. The exported "
        "reading is recalculated from the reviewed black and red digits."
    )
    edited = st.data_editor(
        rows,
        hide_index=True,
        use_container_width=True,
        disabled=["file", "reading_m3", "confidence"],
        column_config={
            "confidence": st.column_config.ProgressColumn(
                "Confidence", min_value=0.0, max_value=1.0, format="percent"
            ),
            "review_required": st.column_config.CheckboxColumn("Review required"),
        },
        key="review_table",
    )

    if hasattr(edited, "to_dict"):
        export_rows = edited.to_dict("records")
    else:
        export_rows = [dict(row) for row in edited]
    for row in export_rows:
        reading = combine_reading(str(row["integer_part"]), str(row["decimal_part"]))
        row["reading_m3"] = reading_text(reading)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(export_rows[0]))
    writer.writeheader()
    writer.writerows(export_rows)
    st.download_button(
        "Download reviewed CSV",
        data=buffer.getvalue(),
        file_name="meter_readings_reviewed.csv",
        mime="text/csv",
        type="primary",
    )
