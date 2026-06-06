"""
Card name links with MTGStocks-style hover image previews.
"""

import html
import uuid

import pandas as pd
import streamlit.components.v1 as components

from core.card_images import load_image_map, scryfall_page_url


def _escape(text: str) -> str:
    return html.escape(str(text), quote=True)


# Shared tooltip positioning — keeps preview inside the Streamlit iframe viewport.
_TOOLTIP_SCRIPT = """
function positionCardTooltip(tooltip, img, clientX, clientY, anchorEl) {
  const pad = 10;
  const vw = window.innerWidth;
  const vh = window.innerHeight;

  tooltip.style.display = 'block';
  img.style.maxHeight = '';
  img.style.width = '244px';

  let tw = tooltip.offsetWidth || 260;
  let th = tooltip.offsetHeight || 340;

  let x, y;
  if (anchorEl) {
    const r = anchorEl.getBoundingClientRect();
    x = r.right + pad;
    y = r.top + (r.height / 2) - (th / 2);
    if (x + tw > vw - pad) {
      x = r.left - tw - pad;
    }
    if (y + th > vh - pad) {
      y = vh - th - pad;
    }
    if (y < pad) {
      y = r.bottom + pad;
    }
    if (y + th > vh - pad) {
      y = Math.max(pad, r.top - th - pad);
    }
  } else {
    x = clientX + pad;
    y = clientY + pad;
    if (x + tw > vw - pad) x = clientX - tw - pad;
    if (y + th > vh - pad) y = clientY - th - pad;
    if (y < pad) y = pad;
  }

  x = Math.max(pad, Math.min(x, vw - tw - pad));
  y = Math.max(pad, Math.min(y, vh - th - pad));

  if (th > vh - 2 * pad) {
    const maxH = vh - 2 * pad;
    img.style.maxHeight = maxH + 'px';
    img.style.width = 'auto';
    th = tooltip.offsetHeight || maxH;
    y = Math.max(pad, Math.min(y, vh - th - pad));
  }

  tooltip.style.left = x + 'px';
  tooltip.style.top = y + 'px';
}

function bindCardTooltips(tooltip, img) {
  document.querySelectorAll('.card-link').forEach(link => {
    const place = (e) => {
      if (!img.src) return;
      const cx = e && e.clientX != null ? e.clientX : 0;
      const cy = e && e.clientY != null ? e.clientY : 0;
      positionCardTooltip(tooltip, img, cx, cy, link);
    };
    link.addEventListener('mouseenter', (e) => {
      const src = link.dataset.image;
      if (!src) return;
      const afterLoad = () => place(e);
      img.onload = afterLoad;
      img.src = src;
      if (img.complete) {
        requestAnimationFrame(afterLoad);
      }
    });
    link.addEventListener('mousemove', place);
    link.addEventListener('mouseleave', () => {
      tooltip.style.display = 'none';
      img.src = '';
      img.onload = null;
    });
  });
}
"""


def _tooltip_html(uid: str, body_inner: str, height: int) -> None:
    components.html(
        f"""
<!DOCTYPE html>
<html>
<head>
<style>
  html, body {{ margin: 0; overflow: visible; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; font-size: 14px; }}
  .card-table {{ width: 100%; border-collapse: collapse; }}
  .card-table th, .card-table td {{ padding: 6px 10px; border-bottom: 1px solid #333; text-align: left; }}
  .card-table th {{ background: #1e1e1e; color: #ccc; position: sticky; top: 0; z-index: 1; }}
  .card-table tr:hover {{ background: #2a2a2a; }}
  .card-link {{ color: #6eb5ff; text-decoration: none; cursor: pointer; }}
  .card-link:hover {{ text-decoration: underline; }}
  #tooltip-{uid} {{
    display: none;
    position: fixed;
    z-index: 99999;
    pointer-events: none;
    border-radius: 6px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.6);
    border: 2px solid #444;
    background: #111;
    padding: 2px;
  }}
  #tooltip-{uid} img {{
    display: block;
    width: 244px;
    height: auto;
    border-radius: 4px;
  }}
</style>
</head>
<body>
{body_inner}
<div id="tooltip-{uid}"><img src="" alt="" /></div>
<script>
(function() {{
  const tooltip = document.getElementById('tooltip-{uid}');
  const img = tooltip.querySelector('img');
  {_TOOLTIP_SCRIPT}
  bindCardTooltips(tooltip, img);
}})();
</script>
</body>
</html>
        """,
        height=height,
        scrolling=True,
    )


def render_card_table(
    predictions_df: pd.DataFrame,
    title: str = "Predictions",
    session=None,
):
    """Render predictions table with hoverable card name links."""
    if predictions_df is None or predictions_df.empty:
        import streamlit as st

        st.info("No predictions to display.")
        return

    import streamlit as st

    st.subheader(title)

    if session is None:
        render_card_table_plain(predictions_df)
        return

    card_names = predictions_df["card_name"].tolist() if "card_name" in predictions_df.columns else []
    image_map = load_image_map(session, card_names)

    display_cols = [
        c
        for c in [
            "card_name",
            "opportunity_score",
            "p_included",
            "p_reprint",
            "p_omission",
            "spec_supply_score",
            "has_infinite_loop",
            "combo_with",
            "scarcity_score",
            "demand_score",
            "type_line",
            "edhrec_rank",
        ]
        if c in predictions_df.columns
    ]

    rows_html = []
    for _, row in predictions_df[display_cols].iterrows():
        cells = []
        for col in display_cols:
            val = row[col]
            if col == "card_name":
                name = str(val)
                img = image_map.get(name, "")
                page = scryfall_page_url(name)
                cells.append(
                    f'<td class="name-cell">'
                    f'<a class="card-link" href="{_escape(page)}" target="_blank" '
                    f'data-image="{_escape(img)}" data-name="{_escape(name)}">{_escape(name)}</a>'
                    f"</td>"
                )
            elif col in ("has_infinite_loop",):
                cells.append(f"<td>{'Yes' if val else '—'}</td>")
            elif isinstance(val, float):
                fmt = f"{val:.4f}" if abs(val) < 10 else f"{val:.2f}"
                cells.append(f"<td>{fmt}</td>")
            else:
                cells.append(f"<td>{_escape(val)}</td>")
        rows_html.append(f"<tr>{''.join(cells)}</tr>")

    headers = "".join(f"<th>{_escape(c.replace('_', ' ').title())}</th>" for c in display_cols)
    table_html = (
        f"<table class='card-table'><thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody></table>"
    )

    uid = uuid.uuid4().hex[:8]
    height = min(44 + len(predictions_df) * 36, 800)
    _tooltip_html(uid, table_html, height)


def render_card_table_plain(predictions_df: pd.DataFrame):
    """Fallback without hover when no DB session."""
    import streamlit as st

    st.dataframe(predictions_df, use_container_width=True, hide_index=True)


def render_card_name_list(card_names: list, session, title: str = None):
    """Render a list of card names with hover previews."""
    import streamlit as st

    if title:
        st.markdown(f"**{title}**")
    if not card_names:
        st.write("—")
        return

    image_map = load_image_map(session, card_names)
    links = []
    for name in card_names:
        img = image_map.get(name, "")
        page = scryfall_page_url(name)
        links.append(
            f'<a class="card-link" href="{_escape(page)}" target="_blank" '
            f'data-image="{_escape(img)}">{_escape(name)}</a>'
        )

    uid = uuid.uuid4().hex[:8]
    height = min(80 + len(card_names) // 4 * 24, 400)
    _tooltip_html(uid, f"<p>{', '.join(links)}</p>", height)
