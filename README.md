# s2pull

A small command-line tool that pulls the most-cited recent papers from the
[Semantic Scholar](https://www.semanticscholar.org/product/api) API and writes
them to a CSV. Give it a few topics and it returns a ranked list with the title,
citation counts, venue, arXiv ID, and an open-access PDF link when one exists.
It saves you from digging through Google Scholar by hand when you want to see
what is getting cited in your area.

## Install

Requires Python 3.8 or newer and the `requests` library.

```bash
pip install -r requirements.txt
```

No API key is needed to get started. For large pulls, a free Semantic Scholar
API key raises the rate limit; pass it with `--api-key`.

## Quick start

```bash
python3 s2pull.py --topic "language model" "diffusion model"
```

This writes a CSV (for example `top_2026_none.csv`) to the current folder, with
the highest-cited matching papers first.

## Options

| Flag | What it does |
|------|--------------|
| `--topic WORDS...` | One or more topics, combined with OR. Quote multi-word topics. Defaults to a broad LLM/VLM/AI set. |
| `--normalized {none,week,month}` | Rank by raw citations (`none`) or by citations per week/month. Per-week is good for catching fast risers that are too new to have piled up citations. |
| `--sort {citations,influential}` | Base ranking signal. `influential` uses Semantic Scholar's influential-citation count, which filters out throwaway citations. |
| `--start MM/YYYY` | Start of the date window, for example `05/2025`. Open-ended if no end is given. |
| `--end MM/YYYY` | End of the date window. |
| `--venue NAMES...` | Restrict to specific venues, for example `NeurIPS ICML ICLR ACL EMNLP`. |
| `--min-cites N` | Drop papers below this citation count. |
| `--exact-match WORDS...` | Require all of these terms in the paper (matched against title and abstract, case-insensitive). |
| `--abstract` | Add an abstract column to the output. |
| `--max N` | Number of rows in the output (default 100). |
| `--pool N` | How many papers to pull before ranking. Auto-bumps when normalizing. |
| `--out FILE` | Output filename. |
| `--api-key KEY` | Optional Semantic Scholar API key for higher rate limits. |

## Examples

Most-cited LLM and VLM papers of 2026:

```bash
python3 s2pull.py --topic "large language model" "vision language model" --max 50
```

Fastest-climbing papers since May 2025, ranked by influential citations per week:

```bash
python3 s2pull.py --start 05/2025 --normalized week --sort influential
```

Papers about steering that also discuss LVLM:

```bash
python3 s2pull.py --exact-match "MT-bench" "LVLM"
```

Top NeurIPS and ICML efficiency papers with at least 20 citations:

```bash
python3 s2pull.py --topic "efficient inference" "quantization" "speculative decoding" \
  --venue NeurIPS ICML --min-cites 20
```

## Notes and caveats

- Ranking by raw citations early in a year favors papers published in January and
  February, since they have had more time to accumulate citations. Use
  `--normalized week` for a fairer view of what is actually picking up.
- `--exact-match` is matched server-side against the title and abstract with
  stemming, so "steering" also catches "steer" and "steered", and a term in the
  title alone can qualify. A light client-side check tightens this to the abstract
  where the abstract is available. Some publishers do not expose abstracts through
  the public API, so a few relevant papers may be missed.
- Semantic Scholar assigns fields of study with a classifier, so the
  Computer Science filter is good but not perfect.

## Contributing

Feel free to improve on it and add features. Open an issue or a pull request.
A few ideas that would be useful:

- A `--strict` mode that verifies abstracts through the `/paper/batch` endpoint
  for an exact guarantee.
- A watch mode that stores citation counts between runs and reports new and
  climbing papers, so you can run it weekly as a standing alert.
- An OpenAlex backend as an alternative source.

It is meant to be hacked on.
