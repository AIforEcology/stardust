# Pricing data

`model_prices_and_context_window.json` comes from [BerriAI/litellm](https://github.com/BerriAI/litellm) (MIT License, © Berri AI). Current copy: litellm commit `320b40645c48d77b6d5ffae0f5ca0cfee73442a4` (fetched 2026-09-23, 3,467 priced models). Refresh it with:

```bash
python scripts/update_pricing.py --ref <litellm commit SHA>
```

If the file is missing, Core still runs but `cost_usd` is `null` and the indicator's cost tier is `X`.
