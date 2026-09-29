.PHONY: data eval eval-claude app test all
export PYTHONPATH := src

data:        ## generate the synthetic alert queue
	python -m amlagent.data_gen
eval:        ## run and score the rules agents (v1 transactions only, v2 with identity/network) and compare
	python -m amlagent.evaluate --agent baseline_v1 --no-audit
	python -m amlagent.evaluate --agent baseline
	python -m amlagent.compare
eval-claude: ## run and score the Claude agent (needs ANTHROPIC_API_KEY)
	python -m amlagent.evaluate --agent claude
	python -m amlagent.compare
app:         ## open the reviewer workbench
	streamlit run app.py
test:
	python -m pytest -q
all: data test eval
