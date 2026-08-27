"""Generate a local, visual-first review page for a very small pilot."""

from __future__ import annotations

import html
import json
import os
from dataclasses import dataclass
from pathlib import Path

from surveillance_video_agent.db import CandidateDatabase, utc_now


@dataclass(frozen=True, slots=True)
class PilotReviewExport:
    output_path: Path
    feedback_template_path: Path
    video_count: int


def export_pilot_review(
    database: CandidateDatabase,
    output_path: Path,
    *,
    campaign_ids: tuple[str, ...] = (
        "demand_action_v1",
        "fight_confounder_v1",
    ),
    run_ids: tuple[str, ...] | None = None,
) -> PilotReviewExport:
    """Write an offline page that plays downloaded videos and exports JSON feedback."""

    placeholders = ",".join("?" for _ in campaign_ids)
    run_filter = ""
    parameters: tuple[str, ...] = campaign_ids
    if run_ids:
        run_placeholders = ",".join("?" for _ in run_ids)
        run_filter = f" AND q.run_id IN ({run_placeholders})"
        parameters = (*campaign_ids, *run_ids)
    rows = database.connection.execute(
        f"""
        SELECT q.candidate_key, q.campaign_id, q.subtype, q.rank,
               c.title, c.platform, c.source_url,
               c.camera_pool, i.vector_similarity, m.final_path
        FROM queue_assignments q
        JOIN candidates c ON c.candidate_key = q.candidate_key
        JOIN secondary_batch_items i
          ON i.batch_id = q.batch_id AND i.candidate_key = q.candidate_key
        JOIN media_objects m ON m.candidate_key = q.candidate_key
        WHERE q.campaign_id IN ({placeholders})
          {run_filter}
          AND c.status = 'downloaded'
          AND m.publish_status = 'published'
        ORDER BY q.queued_at, q.rank, q.candidate_key
        """,
        parameters,
    ).fetchall()
    destination = Path(output_path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    items = []
    for row in rows:
        media_path = Path(row["final_path"]).resolve()
        if not media_path.is_file():
            continue
        items.append(
            {
                "candidate_key": row["candidate_key"],
                "campaign_id": row["campaign_id"],
                "shown_subtype": row["subtype"],
                "title": row["title"],
                "platform": row["platform"],
                "camera_pool": row["camera_pool"],
                "source_url": row["source_url"],
                "similarity": row["vector_similarity"],
                "media_path": str(media_path),
                "media_url": Path(
                    os.path.relpath(media_path, destination.parent)
                ).as_posix(),
                "source_correct": None,
                "task_usable": None,
                "corrected_subtype": "",
                "notes": "",
            }
        )
    # The HTML page and its JSON starter must travel as one batch-scoped pair.
    # A fixed filename would let the next export overwrite the prior batch's
    # template even though the associated videos and labels are distinct.
    template_path = destination.with_name(
        destination.stem + "_feedback_template.json"
    )
    template = {
        "schema_version": "pilot_feedback_v1",
        "generated_at": utc_now(),
        "instructions": "在本地可视页面标注后点击‘导出反馈 JSON’，将文件交回。",
        "labels": [
            {
                key: item[key]
                for key in (
                    "candidate_key",
                    "campaign_id",
                    "shown_subtype",
                    "camera_pool",
                    "source_correct",
                    "task_usable",
                    "corrected_subtype",
                    "notes",
                )
            }
            for item in items
        ],
    }
    _atomic_write(
        template_path,
        json.dumps(template, ensure_ascii=False, indent=2) + "\n",
    )
    serialized = json.dumps(items, ensure_ascii=False).replace("<", "\\u003c")
    cards = "\n".join(_card(item, index) for index, item in enumerate(items, 1))
    page = _PAGE.format(
        video_count=len(items),
        cards=cards or '<p class="empty">暂无技术验证成功的下载视频。</p>',
        items_json=serialized,
    )
    _atomic_write(destination, page)
    return PilotReviewExport(destination, template_path, len(items))


def _card(item: dict, index: int) -> str:
    key = html.escape(item["candidate_key"], quote=True)
    source_question = (
        "这是真实手机/短视频拍摄（非影视、游戏、教程或广告）吗？"
        if item.get("camera_pool") == "mobile_adjacent"
        else "这是固定监控/安防摄像头画面吗？"
    )
    return f"""
    <article class="card" data-key="{key}">
      <h2>{index}. {html.escape(item['shown_subtype'])}</h2>
      <video controls preload="metadata" src="{html.escape(item['media_url'], quote=True)}"></video>
      <p class="title">{html.escape(item['title'] or '（无标题）')}</p>
      <p class="meta">{html.escape(item['platform'])} · 相似度 {float(item['similarity']):.3f} · {key}</p>
      <div class="question"><span>{html.escape(source_question)}</span>
        <label><input type="radio" name="source-{index}" value="true"> 是</label>
        <label><input type="radio" name="source-{index}" value="false"> 否</label>
        <label><input type="radio" name="source-{index}" value="null" checked> 不确定</label>
      </div>
      <div class="question"><span>这条视频对当前任务可用吗？</span>
        <label><input type="radio" name="usable-{index}" value="true"> 是</label>
        <label><input type="radio" name="usable-{index}" value="false"> 否</label>
        <label><input type="radio" name="usable-{index}" value="null" checked> 不确定</label>
      </div>
      <label class="field">如果分类不对，写下正确类型（可空）
        <input class="corrected" type="text">
      </label>
      <label class="field">备注（可空）<textarea class="notes" rows="2"></textarea></label>
    </article>"""


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


_PAGE = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>监控候选小样本可视标注</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:#f4f6f8;color:#17202a}}
main{{max-width:1000px;margin:auto;padding:24px}} header{{position:sticky;top:0;background:#f4f6f8ee;padding:8px 0 14px;z-index:2}}
.card{{background:white;border:1px solid #d8dee4;border-radius:14px;padding:18px;margin:18px 0;box-shadow:0 2px 10px #0000000d}}
video{{display:block;width:100%;max-height:560px;background:#111;border-radius:10px}} h1{{margin:0 0 8px}} h2{{margin-top:0}}
.title{{font-weight:600}} .meta{{color:#59636e;font-size:13px;word-break:break-all}} .question{{margin:14px 0}}
.question span{{display:block;font-weight:600;margin-bottom:7px}} .question label{{margin-right:18px}} .field{{display:block;margin-top:12px;font-weight:600}}
.field input,.field textarea{{display:block;box-sizing:border-box;width:100%;margin-top:6px;padding:9px;border:1px solid #b8c0c8;border-radius:7px;font:inherit}}
button{{background:#1167d8;color:white;border:0;border-radius:8px;padding:11px 18px;font-weight:700;cursor:pointer}} .hint{{color:#46515c}}
</style></head><body><main>
<header><h1>监控候选小样本</h1><p class="hint">共 {video_count} 条。先看画面，每条只回答两个问题；系统会自动保存在本机浏览器。</p><button id="export">导出反馈 JSON</button></header>
{cards}
</main><script>
const items={items_json}; const storageKey='surveillance-pilot-feedback-v1';
const saved=JSON.parse(localStorage.getItem(storageKey)||'{{}}');
function value(card,name){{const x=card.querySelector(`input[name^="${{name}}-"]:checked`);return x.value==='true'?true:x.value==='false'?false:null;}}
function collect(){{const labels=[];document.querySelectorAll('.card').forEach((card,i)=>{{labels.push({{candidate_key:items[i].candidate_key,campaign_id:items[i].campaign_id,shown_subtype:items[i].shown_subtype,camera_pool:items[i].camera_pool,source_correct:value(card,'source'),task_usable:value(card,'usable'),corrected_subtype:card.querySelector('.corrected').value.trim(),notes:card.querySelector('.notes').value.trim()}})}});return {{schema_version:'pilot_feedback_v1',exported_at:new Date().toISOString(),labels}};}}
function persist(){{localStorage.setItem(storageKey,JSON.stringify(collect()));}}
document.querySelectorAll('input,textarea').forEach(x=>x.addEventListener('change',persist));
if(saved.labels){{saved.labels.forEach((x,i)=>{{const card=document.querySelectorAll('.card')[i];if(!card)return;['source_correct','task_usable'].forEach((k,j)=>{{const v=x[k]===true?'true':x[k]===false?'false':'null';const names=j===0?'source':'usable';const radio=card.querySelector(`input[name^="${{names}}-"][value="${{v}}"]`);if(radio)radio.checked=true;}});card.querySelector('.corrected').value=x.corrected_subtype||'';card.querySelector('.notes').value=x.notes||'';}})}}
document.getElementById('export').addEventListener('click',()=>{{const blob=new Blob([JSON.stringify(collect(),null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='pilot_feedback.json';a.click();URL.revokeObjectURL(a.href);}});
</script></body></html>"""
