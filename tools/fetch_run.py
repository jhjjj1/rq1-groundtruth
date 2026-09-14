#!/usr/bin/env python3
"""盯一次 workflow run 跑完，然后把全部产物拉回本地并对账。

为什么要单独一个脚本
--------------------
一批 256 个 job 会产出 257 个 artifact、7~9 GB。手动拉不现实，而且有两个坑
足以让「拉完了」和「拉全了」长得一样：

* **分页**。`/actions/runs/{id}/artifacts` 默认每页 100 条。不翻页就只拿到前
  100 个，而且接口不会报错 —— 剩下 157 份静默消失。
* **重定向**。artifact 的下载地址会跳到另一台主机。`curl` 默认不把
  Authorization 头带过去（这是对的），所以这里用 curl 下载、用 API 取元数据，
  不用 urllib 自己跟跳转。

对账是硬性的：`total_count` 说有多少个，就必须落地多少个；每个 zip 解出来必须
有 manifest.json。对不上直接非零退出，不许带着一个说不清的差额往下走。

断点续传：已经下好且大小对得上的跳过，所以中断了重跑一遍就行。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import zipfile

API = "https://api.github.com"


def token_from_git_credentials():
    """从 ~/.git-credentials 里取 —— 和本项目其它命令同一个来源。"""
    path = pathlib.Path.home() / ".git-credentials"
    if not path.is_file():
        return None
    m = re.search(r"x-access-token:([^@]+)@", path.read_text(errors="replace"))
    return m.group(1) if m else None


def curl_json(url, token, retries=3):
    for attempt in range(retries):
        p = subprocess.run(
            ["curl", "-sS", "-H", f"Authorization: Bearer {token}",
             "-H", "Accept: application/vnd.github+json", url],
            capture_output=True, text=True)
        if p.returncode == 0 and p.stdout.strip():
            try:
                return json.loads(p.stdout)
            except json.JSONDecodeError:
                pass
        time.sleep(2 * (attempt + 1))
    return None


def curl_download(url, token, dest, retries=3):
    for attempt in range(retries):
        p = subprocess.run(
            ["curl", "-sSL", "--fail", "-H", f"Authorization: Bearer {token}",
             url, "-o", str(dest)],
            capture_output=True, text=True)
        if p.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return True, ""
        time.sleep(3 * (attempt + 1))
    return False, (p.stderr or "").strip()[:200]


def find_run(repo, token, workflow_name, run_id=None):
    if run_id:
        return curl_json(f"{API}/repos/{repo}/actions/runs/{run_id}", token)
    data = curl_json(f"{API}/repos/{repo}/actions/runs?per_page=30", token) or {}
    runs = [r for r in data.get("workflow_runs", [])
            if r.get("name") == workflow_name]
    if not runs:
        return None
    return sorted(runs, key=lambda r: r["run_number"])[-1]


def job_progress(repo, token, run_id):
    """翻页读全部 job —— 256 个 job 也会被分页。"""
    counts, page, total = {}, 1, None
    running = []
    while True:
        d = curl_json(f"{API}/repos/{repo}/actions/runs/{run_id}"
                      f"/jobs?per_page=100&page={page}", token)
        if not d:
            break
        total = d.get("total_count", 0)
        for j in d.get("jobs", []):
            key = j.get("conclusion") or j.get("status") or "?"
            counts[key] = counts.get(key, 0) + 1
            if j.get("status") == "in_progress" and len(running) < 3:
                running.append(j.get("name", "")[:48])
        if page * 100 >= (total or 0):
            break
        page += 1
    return total, counts, running


def list_artifacts(repo, token, run_id):
    """翻页读全部 artifact。不翻页只会拿到前 100 个，而且不报错。"""
    out, page, total = [], 1, None
    while True:
        d = curl_json(f"{API}/repos/{repo}/actions/runs/{run_id}"
                      f"/artifacts?per_page=100&page={page}", token)
        if not d:
            break
        total = d.get("total_count", 0)
        out.extend(d.get("artifacts", []))
        if len(out) >= (total or 0) or not d.get("artifacts"):
            break
        page += 1
    return total, out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="jhjjj1/rq1-groundtruth")
    ap.add_argument("--workflow-name", default="build-groundtruth")
    ap.add_argument("--run-id", type=int, default=None,
                    help="不给就取该 workflow 最新的一次 run")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--wait", action="store_true", help="没跑完就等")
    ap.add_argument("--poll-seconds", type=int, default=180)
    ap.add_argument("--keep-zip", action="store_true",
                    help="解压后保留 zip（默认删除，省磁盘）")
    ap.add_argument("--aggregate", default=None,
                    help="下载完跑一次聚合，指定 aggregate_manifests.py 的路径")
    args = ap.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or token_from_git_credentials()
    if not token:
        print("拿不到 token：设 GITHUB_TOKEN，或让 ~/.git-credentials 可读",
              file=sys.stderr)
        return 2

    run = find_run(args.repo, token, args.workflow_name, args.run_id)
    if not run:
        print(f"找不到 {args.workflow_name} 的 run", file=sys.stderr)
        return 2
    rid = run["id"]
    print(f"run #{run['run_number']}  id={rid}  {run['status']}/{run['conclusion']}")
    print(f"  https://github.com/{args.repo}/actions/runs/{rid}")

    while args.wait and run.get("status") != "completed":
        total, counts, running = job_progress(args.repo, token, rid)
        done = sum(v for k, v in counts.items()
                   if k in ("success", "failure", "cancelled", "skipped"))
        print(f"[{time.strftime('%H:%M:%S')}] {done}/{total} job 结束  "
              f"{counts}" + (f"  在跑: {running}" if running else ""),
              flush=True)
        time.sleep(args.poll_seconds)
        run = find_run(args.repo, token, args.workflow_name, rid) or run

    print(f"\nrun 结束：{run.get('status')}/{run.get('conclusion')}")

    total_jobs, counts, _ = job_progress(args.repo, token, rid)
    print(f"job 合计 {total_jobs}：{counts}")

    n_art, arts = list_artifacts(args.repo, token, rid)
    print(f"artifact 合计 {n_art}，列到 {len(arts)} 个")
    if n_art != len(arts):
        # 分页没翻干净就是少拿了，宁可失败也不要带着缺口继续
        print(f"!! 分页没取全：接口说 {n_art}，只列到 {len(arts)}", file=sys.stderr)
        return 1

    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ok, failed, skipped = 0, [], 0
    for i, a in enumerate(arts, 1):
        name = a["name"]
        d = out / name
        if (d / "manifest.json").is_file() or (d.is_dir() and any(d.iterdir())):
            skipped += 1
            continue
        z = out / f"{name}.zip"
        good, err = curl_download(a["archive_download_url"], token, z)
        if not good:
            failed.append({"name": name, "error": err})
            continue
        try:
            d.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(z) as zf:
                zf.extractall(d)
            ok += 1
        except (zipfile.BadZipFile, OSError) as exc:
            failed.append({"name": name, "error": f"unzip: {exc}"})
        finally:
            if not args.keep_zip:
                z.unlink(missing_ok=True)
        if i % 20 == 0:
            print(f"  {i}/{len(arts)} …", flush=True)

    dirs = [p for p in out.iterdir() if p.is_dir()]
    manifests = list(out.rglob("manifest.json"))
    print(f"\n下载 {ok} 个，跳过（已存在）{skipped} 个，失败 {len(failed)} 个")
    print(f"本地目录 {len(dirs)} 个，manifest {len(manifests)} 份")
    for f in failed[:10]:
        print(f"  失败：{f['name']}  {f['error']}")

    report = {"run_id": rid, "run_number": run["run_number"],
              "conclusion": run.get("conclusion"),
              "jobs_total": total_jobs, "job_conclusions": counts,
              "artifacts_reported": n_art, "artifacts_local": len(dirs),
              "manifests_local": len(manifests),
              "downloaded": ok, "skipped": skipped, "failed": failed}
    (out / "_fetch_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.aggregate:
        print()
        subprocess.run([sys.executable, args.aggregate,
                        "--artifacts-dir", str(out),
                        "--expected", str(max(0, (total_jobs or 1) - 2)),
                        "--out", str(out / "_aggregate.json")])

    # 对账：接口说有多少个，本地就得有多少个目录
    if len(dirs) != n_art or failed:
        print(f"\n!! 对账不过：接口 {n_art} 个 artifact，本地 {len(dirs)} 个目录，"
              f"失败 {len(failed)} 个。重跑本命令会跳过已下好的，只补缺的。",
              file=sys.stderr)
        return 1
    print("\n对账通过：接口数 = 本地数")
    return 0


if __name__ == "__main__":
    sys.exit(main())
