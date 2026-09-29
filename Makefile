.PHONY: data eval learn eval-claude app test all
export PYTHONPATH := src

data:        ## generate the synthetic alert queue (transactions, identity, activity)
	python -m amlagent.data_gen
eval:        ## score rules v1 (transactions), v2 (+ logins/network/disputes), v3 (+ account activity)
	python -m amlagent.evaluate --agent baseline_v1 --no-audit
	python -m amlagent.evaluate --agent baseline_v2 --no-audit
	python -m amlagent.evaluate --agent baseline
learn:       ## demo: an analyst decides half the queue, train on it, score on the other half
	python -m amlagent.learning simulate --share 0.5
	python -m amlagent.learning train
	python -m amlagent.evaluate --agent learned --no-audit
	python -m amlagent.compare
eval-claude: ## run and score the Claude agent (needs ANTHROPIC_API_KEY)
	python -m amlagent.evaluate --agent claude
	python -m amlagent.compare
app:         ## open the workbench
	streamlit run app.py
test:
	python -m pytest -q
all: data test eval learn
