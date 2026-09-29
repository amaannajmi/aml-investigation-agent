.PHONY: data eval eval-claude app test all
export PYTHONPATH := src

data:        ## generate the synthetic alert queue
	python -m amlagent.data_gen
eval:        ## run and score the deterministic baseline agent
	python -m amlagent.evaluate --agent baseline
eval-claude: ## run and score the Claude agent (needs ANTHROPIC_API_KEY)
	python -m amlagent.evaluate --agent claude
app:         ## open the reviewer workbench
	streamlit run app.py
test:
	python -m pytest -q
all: data test eval
