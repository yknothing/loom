"""Weekly knowledge digest — data aggregation, HTML rendering, Resend delivery."""
import html as html_lib
import os
from datetime import datetime, timezone

import resend

WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

CATEGORY_ZH = {
    "ai": "人工智能", "engineering": "工程", "business": "商业",
    "science": "科学", "culture": "文化", "opinion": "观点",
    "security": "安全", "hardware": "硬件", "other": "其他",
}


def render_digest_html(data: dict, app_url: str = "") -> str:
    """Inline-CSS, table-layout HTML email (zh-CN)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cats = " · ".join(
        f"{html_lib.escape(CATEGORY_ZH.get(c, str(c)))} {n}" for c, n in
        sorted(data["categories"].items(), key=lambda x: -x[1])[:5]
    ) or "—"

    rows = ""
    for it in data["items"]:
        title = html_lib.escape(it.get("title_zh") or it.get("title_en") or "Untitled")
        summary = html_lib.escape((it.get("summary_zh") or "")[:160])
        q = it.get("quality_score")
        q_str = f"{float(q):.1f}" if q is not None else "—"
        tags = " ".join(
            f'<span style="display:inline-block;border:1px solid #E4E4E7;border-radius:3px;'
            f'padding:1px 6px;font-size:11px;color:#71717A;margin-right:4px;">{html_lib.escape(str(t))}</span>'
            for t in (it.get("tags") or [])[:4]
        )
        rows += f"""
        <tr>
          <td style="padding:14px 0;border-bottom:1px solid #E4E4E7;">
            <div style="font-size:15px;font-weight:600;color:#09090B;">{title}
              <span style="font-family:monospace;font-size:11px;color:#002FA7;margin-left:6px;">Q {q_str}</span>
            </div>
            <div style="font-size:13px;color:#52525B;margin-top:4px;line-height:1.6;">{summary}…</div>
            <div style="margin-top:6px;">{tags}</div>
          </td>
        </tr>"""

    if not rows:
        rows = """<tr><td style="padding:24px 0;color:#71717A;font-size:13px;">
        本周没有新的编译结果。提交文章或运行 Pipeline 后，下周简报会更精彩。</td></tr>"""

    link = (
        f'<a href="{app_url}" style="color:#002FA7;text-decoration:none;font-weight:600;">'
        f"打开 Loom 控制台 →</a>" if app_url else ""
    )

    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#F4F4F5;font-family:'Helvetica Neue',Arial,'PingFang SC','Microsoft YaHei',sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#F4F4F5;padding:32px 0;">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="background:#FFFFFF;border:1px solid #E4E4E7;border-radius:6px;">
  <tr><td style="padding:28px 32px;border-bottom:2px solid #002FA7;">
    <div style="font-family:monospace;font-size:20px;font-weight:700;color:#09090B;">LOOM<span style="color:#002FA7;">_</span></div>
    <div style="font-size:11px;letter-spacing:2px;color:#71717A;margin-top:2px;">每周知识简报 · {today}</div>
  </td></tr>
  <tr><td style="padding:24px 32px;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td style="text-align:center;padding:10px;background:#F4F4F5;border-radius:4px;">
          <div style="font-size:24px;font-weight:700;color:#002FA7;">{data['total']}</div>
          <div style="font-size:11px;color:#71717A;">本周编译文章</div>
        </td>
        <td style="width:8px;"></td>
        <td style="text-align:center;padding:10px;background:#F4F4F5;border-radius:4px;">
          <div style="font-size:24px;font-weight:700;color:#09090B;">{data['tokens'] // 1000}k</div>
          <div style="font-size:11px;color:#71717A;">Token 消耗</div>
        </td>
        <td style="width:8px;"></td>
        <td style="text-align:center;padding:10px;background:#F4F4F5;border-radius:4px;">
          <div style="font-size:24px;font-weight:700;color:#D97706;">{data['review_pending']}</div>
          <div style="font-size:11px;color:#71717A;">待审查项</div>
        </td>
      </tr>
    </table>
    <div style="font-size:12px;color:#71717A;margin-top:14px;">分类分布：{cats}</div>
  </td></tr>
  <tr><td style="padding:0 32px 8px;">
    <div style="font-size:11px;letter-spacing:2px;color:#71717A;font-weight:600;">本周精选（按质量评分）</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>
  </td></tr>
  <tr><td style="padding:20px 32px 28px;">{link}</td></tr>
  <tr><td style="padding:16px 32px;background:#F4F4F5;border-radius:0 0 6px 6px;">
    <div style="font-size:11px;color:#A1A1AA;">由 Loom 知识编译 Pipeline 自动生成 · 可在控制台「设置」中调整推送频率</div>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def send_email(to_list: list, subject: str, html: str) -> dict:
    """Send via Resend (sync — call through asyncio.to_thread)."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    if not api_key:
        raise RuntimeError("未配置 RESEND_API_KEY — 请在 backend/.env 中设置后重启后端")
    resend.api_key = api_key
    params = {
        "from": os.environ.get("SENDER_EMAIL", "onboarding@resend.dev"),
        "to": to_list,
        "subject": subject,
        "html": html,
    }
    return resend.Emails.send(params)
