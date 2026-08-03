@echo off
:: Activate the Conda environment where compatible pandas and numpy are installed
call conda activate mentalhealth

:: Run the scoring script with the desired options (deterministic uses all columns,
:: LLM uses the top 100 most predictive features – the script handles that internally)
python "%~dp0score_industries.py" --max-llm-stocks 5 --max-model-fields 0

:: Deactivate environment (optional)
call conda deactivate