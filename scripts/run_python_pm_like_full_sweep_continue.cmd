@echo off
cd /d "C:\Users\Efthymis\OneDrive - University College Dublin\power-systems-tools-learning"
"C:\Users\Efthymis\miniconda3\envs\opf\python.exe" -u "python\own_models\scripts\34_run_own_vs_powermodels_pm_like_full_sweep.py" --max-iter 500 --skip-scenarios 1888_rte_angle_10p0deg__system_angle_limits__none__none__angle_limit_deg__10p0 1888_rte_load_x1p4__load_scaling__none__none__load_multiplier__1p4 > "results\own_vs_powermodels\_logs\python_pm_like_full_sweep_continue_active.out.log" 2> "results\own_vs_powermodels\_logs\python_pm_like_full_sweep_continue_active.err.log"
