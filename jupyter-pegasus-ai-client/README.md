# PegasusAI Bot Client

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

## About

The PegasusAI Bot Client is a JupyterLab extension that provides a notebook-integrated AI assistant interface. From within a notebook, users can submit code, output, and error information from a selected cell to the PegasusAI Bot Server and receive explanations, debugging guidance, and suggested corrective actions. The extension features a cell toolbar button for one-click AI analysis, enabling a streamlined AI-assisted debugging workflow directly within JupyterLab.

## Dependencies

- Python >= 3.10
- Node.js

You will need Node.js to build the extension package. You may install it from [nodejs.org](https://nodejs.org/en/download). We
recommend using the latest LTS version of Node.js.

## Installation

1.  Clone the repository and enter the directory
2.  Create and activate a Python virtual environment:
    ```bash
    python -m venv .venv
    source .venv/bin/activate
    ```
3.  Install the Python package in development mode:
    ```bash
    pip install --editable "."
    ```
4.  Link the extension with JupyterLab:
    ```bash
    jupyter-builder develop . --overwrite
    ```
5.  Build the TypeScript source:
    ```bash
    jlpm build
    ```
6.  Launch JupyterLab:
    ```bash
    jupyter lab
    ```

For development, run jlpm watch in a separate terminal so changes rebuild automatically.

## How to use the Extension

<pre>
1. Open JupyterLab on the browser

2. Enter your PegasusAI Bot settings (URL and User ID), then click Save

<img align="center" src="figures/pegasusai_bot_settings.png" alt="PegasusAI bot button" width="40%">

3. Create a new notebook and add a cell

4. Look for the PegasusAI bot extension button -- it is the first icon in the cell toolbar

<img align="center" src="figures/pegasusai_bot_button.png" alt="PegasusAI bot button" width="70%">

5. Enter code within the cell, and click the PegasusAI bot button

<img src="figures/code_to_test.png" alt="Test code" width="70%">

6. The extension processes the code and error using an AI model at the backend and the response is displayed on the cell output

  <img src="figures/response_from_bot.png" alt="Response from the server" width="60%">

7. A feedback widget will also appear in the cell output. You can optionally rate the response using the Likert scale (Yes/Somewhat/No)

  <img src="figures/feedback_widget.png" alt="Feedback widget" width="60%">

8. When the server is offline, an error message is displayed

  <img src="figures/server_offline_notification.png" alt="Server offline" width="60%">

</pre>

## Contact Us

Dr. Michela Taufer: mtaufer@utk.edu

Dr. Sai Swaminathan: sai@utk.edu

## Copyright and License

Copyright 2026 Global Computing Lab and EPIC Lab.

PegasusAI Bot Client is distributed under the terms of the [Apache License, Version 2.0](https://spdx.org/licenses/Apache-2.0.html).

See [LICENSE](./LICENSE) and [COPYRIGHT](./COPYRIGHT) for more details.

SPDX-License-Identifier: Apache-2.0

## Acknowledgements

This project is funded by the National Science Foundation (NSF) under grant number [#2513101](https://www.nsf.gov/awardsearch/show-award?AWD_ID=2513101). Any opinions, findings, and conclusions, or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the National Science Foundation.
