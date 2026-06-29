#!/usr/bin/env python3
"""
Pull top-cited papers from Semantic Scholar's bulk endpoint.

Examples:
  python s2pull.py                               # raw citations, default LLM/VLM/AI topics
  python s2pull.py --normalized week             # rank by citations/week (surfaces fast risers)
  python s2pull.py --normalized month --max 200
  python s2pull.py --topic "speculative decoding" "kv cache" "mixture of experts" --max 50
  python s2pull.py --start 05/2025 --normalized week --out top_2025_perweek.csv
  python s2pull.py --venue NeurIPS ICML ICLR --min-cites 20 --sort influential
  python s2pull.py --topic "steering vector" --abstract --max 30
"""
import argparse, csv, time, sys, re, hashlib
from datetime import date, datetime

URL = "https://api.semanticscholar.org/graph/v1/paper/search/bulk"

DEFAULT_TOPICS = [
    "large language model", "language model", "LLM",
    "vision language model", "VLM", "multimodal",
    "diffusion model", "transformer", "reinforcement learning",
]


def _quote(t):
    # quote anything not purely alphanumeric so S2 treats it as a phrase, not
    # operators: spaces, and especially '-' which is S2's NOT operator
    # (unquoted "MT-bench" would parse as: MT AND NOT bench).
    return t if t.isalnum() else f'"{t}"'


def build_query(topics, must=None):
    # S2 bulk boolean: | = OR, + = AND, "..." = phrase, () = grouping.
    # keywords are matched against each paper's title AND abstract server-side.
    parts = [_quote(t) for t in topics]
    q = " | ".join(parts)
    if must:
        if len(parts) > 1:
            q = f"({q})"           # keep the topic OR-group intact under the AND
        for t in must:
            q += f" + {_quote(t)}"
    return q


def _slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def default_outname(args, date_params):
    # encode only the args that change which papers come back or how they rank.
    # max/pool/out/api-key are deliberately excluded.
    parts = ["papers"]
    if args.topic == DEFAULT_TOPICS:
        parts.append("default")
    else:
        ts = "+".join(_slug(t) for t in args.topic[:3])
        if len(args.topic) > 3:
            ts += f"+{len(args.topic) - 3}more"
        parts.append(ts)
    parts.append(_slug(date_params.get("year") or date_params["publicationDateOrYear"]))
    if args.sort == "influential":
        parts.append("infl")
    if args.normalized != "none":
        parts.append(f"per-{args.normalized}")
    if args.venue:
        parts.append("venue-" + "+".join(_slug(v) for v in args.venue[:3]))
    if args.min_cites is not None:
        parts.append(f"minc{args.min_cites}")
    if args.exact_match:
        parts.append("em-" + "+".join(_slug(t) for t in args.exact_match[:3]))

    stem = "_".join(p for p in parts if p)
    # short config hash so distinct runs never collide, even if the visible
    # parts truncate to the same string
    h = hashlib.md5(stem.encode()).hexdigest()[:6]
    if len(stem) > 100:
        stem = stem[:100].rstrip("-_+")
    return f"{stem}_{h}.csv"


def get_with_retry(requests, url, params, headers, tries=6):
    # unauthenticated calls share a rate pool; bulk pulls will hit 429
    for attempt in range(tries):
        r = requests.get(url, params=params, headers=headers)
        if r.status_code == 429:
            wait = 2 ** attempt  # 1,2,4,8,16,32s
            sys.stderr.write(f"429 rate-limited, sleeping {wait}s\n")
            time.sleep(wait)
            continue
        if r.status_code >= 400:
            # surface S2's reason instead of a bare status code
            sys.stderr.write(f"HTTP {r.status_code}: {r.text[:500]}\n")
        r.raise_for_status()
        return r.json()
    raise RuntimeError("giving up after repeated 429s")


def parse_month(s):
    # "05/2025" -> date(2025, 5, 1)
    m, y = s.split("/")
    return date(int(y), int(m), 1)


def weeks_since(pub_date_str, fallback):
    # prefer full publicationDate; fall back to the window start (or year start)
    try:
        d = datetime.strptime(pub_date_str, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        d = fallback
    days = max((date.today() - d).days, 1)
    return days / 7.0


def main():
    ap = argparse.ArgumentParser(description="Top-cited Semantic Scholar puller")
    ap.add_argument("--topic", nargs="+", default=DEFAULT_TOPICS,
                    help="one or more topic strings, OR'd together")
    ap.add_argument("--normalized", choices=["none", "week", "month"], default="none",
                    help="rank by raw citations (none) or citations per week/month")
    ap.add_argument("--sort", choices=["citations", "influential"], default="citations",
                    help="base ranking signal; --normalized divides this by time if set")
    ap.add_argument("--venue", nargs="+", default=None,
                    help="restrict to these venues, e.g. NeurIPS ICML ICLR ACL EMNLP arXiv.org")
    ap.add_argument("--min-cites", type=int, default=None, dest="min_cites",
                    help="server-side floor on citation count")
    ap.add_argument("--abstract", action="store_true",
                    help="include the abstract column (off by default)")
    ap.add_argument("--exact-match", nargs="+", default=None, dest="exact_match",
                    help="require ALL of these terms in each paper (matched against "
                         "title+abstract server-side, case-insensitive); tightened "
                         "client-side to abstract substrings where available")
    ap.add_argument("--max", type=int, default=100, help="rows in output")
    ap.add_argument("--pool", type=int, default=None,
                    help="how many to pull before ranking; auto-bumps for normalized")
    ap.add_argument("--start", default=None,
                    help="start month MM/YYYY, e.g. 05/2025 (inclusive)")
    ap.add_argument("--end", default=None,
                    help="end month MM/YYYY (inclusive); omit for up-to-today")
    ap.add_argument("--out", default=None)
    ap.add_argument("--api-key", default=None, help="optional S2 API key for higher limits")
    args = ap.parse_args()

    # date filtering: use the plain `year` param unless a month-precision window
    # is given, in which case use publicationDateOrYear with a dated range.
    # (bulk rejects a bare year passed via publicationDateOrYear.)
    start_date = parse_month(args.start) if args.start else date(2026, 1, 1)
    date_params = {}
    if args.start or args.end:
        start_str = start_date.isoformat() if args.start else ""
        end_str = ""
        if args.end:
            em = parse_month(args.end)
            # last day of the end month (jump to next month, step back a day)
            nxt = date(em.year + (em.month == 12), (em.month % 12) + 1, 1)
            from datetime import timedelta
            end_str = (nxt - timedelta(days=1)).isoformat()
        date_params["publicationDateOrYear"] = f"{start_str}:{end_str}"
    else:
        date_params["year"] = "2026"

    # bigger pool only when normalizing (need depth to find fast risers).
    # exact-match no longer needs a bump: the server pre-filters the corpus.
    if args.pool:
        pool = args.pool
    else:
        pool = max(1000, args.max)
        if args.normalized != "none":
            pool = max(3000, args.max * 20)
    out = args.out or default_outname(args, date_params)
    headers = {"x-api-key": args.api_key} if args.api_key else {}

    # request abstract when shown OR when filtering on it. NOTE: the bulk endpoint
    # does NOT support `tldr` (returns 400) — only the relevance/batch endpoints do.
    fields = ["title", "year", "citationCount", "influentialCitationCount",
              "venue", "externalIds", "url", "publicationDate", "openAccessPdf"]
    if args.abstract or args.exact_match:
        fields.append("abstract")

    # server-side sort uses the base signal; time-normalization happens client-side
    sort_field = "citationCount" if args.sort == "citations" else "influentialCitationCount"

    params = {
        "query": build_query(args.topic, args.exact_match),
        "fieldsOfStudy": "Computer Science",
        "sort": f"{sort_field}:desc",
        "fields": ",".join(fields),
    }
    params.update(date_params)
    if args.venue:
        params["venue"] = ",".join(args.venue)
    if args.min_cites is not None:
        params["minCitationCount"] = args.min_cites

    import requests
    papers, token = [], None
    while True:
        if token:
            params["token"] = token
        data = get_with_retry(requests, URL, params, headers)
        papers += data.get("data", [])
        token = data.get("token")
        sys.stderr.write(f"pulled {len(papers)}\n")
        if not token or len(papers) >= pool:
            break
        time.sleep(1)

    # the server query already required these terms in title/abstract. tighten to an
    # exact abstract substring where the abstract is present; keep papers whose
    # abstract came back empty (can't verify, so trust the server-side match).
    if args.exact_match:
        terms = [t.lower() for t in args.exact_match]
        before = len(papers)
        kept = []
        for p in papers:
            ab = (p.get("abstract") or "").lower()
            if not ab or all(t in ab for t in terms):
                kept.append(p)
        papers = kept
        sys.stderr.write(f"exact-match {args.exact_match}: {len(papers)}/{before} kept\n")
        if not papers:
            sys.stderr.write("server returned no papers with all terms; try fewer/looser terms\n")

    # base signal for ranking, mirrors the server-side sort choice
    def base(p):
        key = "citationCount" if args.sort == "citations" else "influentialCitationCount"
        return p.get(key) or 0

    # compute ranking metric
    for p in papers:
        c = base(p)
        if args.normalized == "none":
            p["_metric"] = c
        else:
            w = weeks_since(p.get("publicationDate"), start_date)
            unit = w if args.normalized == "week" else w / 4.345  # ~weeks per month
            p["_metric"] = c / max(unit, 1.0)

    papers.sort(key=lambda p: p["_metric"], reverse=True)

    base_name = "citations" if args.sort == "citations" else "influential"
    metric_col = base_name if args.normalized == "none" else f"{base_name}_per_{args.normalized}"
    cols = [metric_col, "citations", "influential", "pub_date", "title", "venue", "arxiv", "pdf", "url"]
    if args.abstract:
        cols.append("abstract")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for p in papers[:args.max]:
            pdf = (p.get("openAccessPdf") or {}).get("url", "")
            row = [
                round(p["_metric"], 2),
                p.get("citationCount"), p.get("influentialCitationCount"),
                p.get("publicationDate") or p.get("year"),
                p.get("title"),
                p.get("venue"),
                (p.get("externalIds") or {}).get("ArXiv", ""),
                pdf, p.get("url"),
            ]
            if args.abstract:
                row.append(p.get("abstract") or "")
            w.writerow(row)
    sys.stderr.write(f"wrote {out}\n")


if __name__ == "__main__":
    main()
