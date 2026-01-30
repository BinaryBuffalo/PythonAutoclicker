# 🖱️ PythonAutoclicker (v7.5) FINAL
> **High-performance, stealth-focused automation for modern gaming.**

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
![Anti-Cheat](https://img.shields.io/badge/Anti--Cheat-Bypass_Optimized-success)

`PythonAutoclicker` is an advanced input simulation tool designed to mimic human behavior. By combining **Gaussian distribution timing**, **micro-movement jitter**, and **Smart Event Math**, it provides a high level of stealth for competitive titles.

---

## 🌟 Key Features

* **Dual-Logic Modes:**
    * **Standard Mode:** Classic toggle (On/Off).
    * **Smart Mode:** Context-aware Hold-to-Click functionality.
* **Inventory Safety:** Keep the script "Armed" while disabling clicking in menus for seamless inventory management.
* **Humanized Algorithms:** Choose between **Uniform**, **Gaussian (Bell Curve)**, or **Humanized Burst** patterns.
* **Micro-Movements:** Simulates natural hand tremors with adjustable `Shake X/Y` and sensitivity.
* **Asynchronous UI:** Built on **PyQt5** with multi-threading to ensure zero lag during high CPS execution.

---

## 🧠 Smart Mode Logic
To bypass modern detection, the clicker tracks the ratio of Physical vs. Virtual click events using a proprietary state management formula:

$$Check = TotalEvents - (VirtualClicks \times 2)$$

This ensures the application stops the instant physical input is released, perfectly replicating human reaction latency.

---

## 🚀 Installation & Setup

It is highly recommended to run this in a **Python Virtual Environment (venv)** to maintain a clean workspace.

### 1. Clone & Enter Directory
```bash
git clone https://github.com/BinaryBuffalo/PythonAutoclicker.git
cd PythonAutoclicker
python -m venv VM1
source venv/bin/activate
pip install PyQt5 pynput
python aclick3.py
```


