#!/bin/bash
# Nickberg Terminal - Scheduled Scraper
cd /home/nick/nickberg-terminal
source /home/nick/miniconda3/bin/activate
python src/main.py run >> logs/scraper.log 2>&1
