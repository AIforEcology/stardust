# Pricing data

`model_prices_and_context_window.json` comes from [BerriAI/litellm](https://github.com/BerriAI/litellm) (MIT License, © Berri AI). Refresh it with:

```bash
python scripts/update_pricing.py --ref <litellm commit SHA>
```

If the file is missing, Core still runs but `cost_usd` is `null` and the indicator's cost tier is `X`.
