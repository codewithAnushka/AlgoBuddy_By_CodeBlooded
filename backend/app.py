import os
import subprocess
import tempfile
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS

from chat_api import chat_bp


load_dotenv()

app = Flask(__name__)

print("GEMINI KEY:", os.getenv("GEMINI_API_KEY"))

# Proper CORS for local/dev and hosted frontend
CORS(app, resources={r"/*": {"origins": "*"}})

app.register_blueprint(chat_bp)

LOCAL_LANGUAGE_CONFIG = {
    63: {
        "label": "javascript",
        "source_name": "main.js",
        "compile_cmd": None,
        "run_cmd": ["node", "main.js"],
    },
    71: {
        "label": "python",
        "source_name": "main.py",
        "compile_cmd": None,
        "run_cmd": ["python", "main.py"],
    },
}


def _run_process(command, cwd, stdin_text=""):
    return subprocess.run(
        command,
        cwd=cwd,
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=10,
        shell=False,
    )


def run_locally(language_id, source_code, stdin_text=""):
    config = LOCAL_LANGUAGE_CONFIG.get(language_id)
    if not config:
        return (
            {
                "error": (
                    "Local fallback is available only for JavaScript and Python. "
                    "Add JUDGE0_API_KEY to enable all configured languages."
                )
            },
            400,
        )

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir)
            source_path = workdir / config["source_name"]
            source_path.write_text(source_code or "", encoding="utf-8")

            compile_output = ""

            if config["compile_cmd"]:
                compile_result = _run_process(config["compile_cmd"], cwd=workdir)
                if compile_result.returncode != 0:
                    compile_output = compile_result.stderr or compile_result.stdout
                    if not compile_output:
                        compile_output = (
                            f"Compilation failed for {config['label']} with exit code "
                            f"{compile_result.returncode}."
                        )
                    return (
                        {
                            "stdout": "",
                            "stderr": compile_result.stderr,
                            "compile_output": compile_output,
                            "source": "local",
                        },
                        200,
                    )

            run_result = _run_process(config["run_cmd"], cwd=workdir, stdin_text=stdin_text)
            if run_result.returncode != 0:
                runtime_error = run_result.stderr or run_result.stdout
                if not runtime_error:
                    runtime_error = (
                        f"Execution failed for {config['label']} with exit code "
                        f"{run_result.returncode}."
                    )
                return (
                    {
                        "stdout": run_result.stdout,
                        "stderr": runtime_error,
                        "compile_output": compile_output,
                        "source": "local",
                    },
                    200,
                )

            return (
                {
                    "stdout": run_result.stdout,
                    "stderr": run_result.stderr,
                    "compile_output": compile_output,
                    "source": "local",
                },
                200,
            )
    except FileNotFoundError:
        return (
            {
                "error": (
                    f"Local runtime for {config['label']} is not available on this machine. "
                    "Install the runtime or add JUDGE0_API_KEY."
                )
            },
            500,
        )
    except subprocess.TimeoutExpired:
        return ({"error": "Code execution timed out."}, 408)
    except Exception as exc:
        return ({"error": str(exc)}, 500)


def run_with_judge0(language_id, source_code, stdin_text=""):
    url = "https://judge0-ce.p.rapidapi.com/submissions?base64_encoded=false&wait=true"
    headers = {
        "content-type": "application/json",
        "X-RapidAPI-Key": os.getenv("JUDGE0_API_KEY"),
        "X-RapidAPI-Host": "judge0-ce.p.rapidapi.com",
    }
    payload = {
        "language_id": language_id,
        "source_code": source_code,
        "stdin": stdin_text,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        return response.json(), response.status_code
    except Exception as exc:
        return {"error": str(exc)}, 500


@app.route("/", methods=["GET"])
def home():
    return "AlgoBuddy Backend Running", 200


@app.route("/health", methods=["GET"])
def health():
    return (
        jsonify(
            {
                "status": "ok",
                "chat_mode": "gemini" if os.getenv("GEMINI_API_KEY") else "local-fallback",
                "compiler_mode": "judge0" if os.getenv("JUDGE0_API_KEY") else "local-fallback",
                "local_compiler_languages": ["javascript", "python"],
            }
        ),
        200,
    )


@app.route("/compile", methods=["POST"])
def compile_code():
    data = request.get_json() or {}
    language_id = data.get("language_id")
    source_code = data.get("source_code", "")
    stdin_text = data.get("stdin", "")

    if language_id is None:
        return jsonify({"error": "language_id is required"}), 400

    if os.getenv("JUDGE0_API_KEY"):
        result, status_code = run_with_judge0(language_id, source_code, stdin_text)
    else:
        result, status_code = run_locally(language_id, source_code, stdin_text)

    return jsonify(result), status_code


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
