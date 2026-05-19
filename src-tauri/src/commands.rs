use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tokio::sync::Mutex as TokioMutex;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, BufReader};
use tokio::process::Command;
use tokio::time::timeout;
use tauri::ipc::Channel;

/// Shared state holding the currently-running Python child PID so it can be
/// killed by `cancel_translation` from another command.
#[derive(Default)]
pub struct TranslationState {
    pub child_id: TokioMutex<Option<u32>>,
    pub cancelled: AtomicBool,
}

#[derive(serde::Serialize)]
pub struct PdfInfo {
    pub size: u64,
    pub pages: usize,
}

#[tauri::command]
pub async fn get_pdf_info(file_path: String) -> Result<PdfInfo, String> {
    let path = PathBuf::from(&file_path);
    let metadata = std::fs::metadata(&path).map_err(|e| e.to_string())?;
    let size = metadata.len();

    // Use Python to get page count
    let python = find_python();
    let script = format!(
        "import fitz; doc = fitz.open('{}'); print(len(doc)); doc.close()",
        file_path.replace("'", "'\"'\"'")
    );

    let output = Command::new(&python)
        .arg("-c")
        .arg(&script)
        .output()
        .await
        .map_err(|e| e.to_string())?;

    let pages = String::from_utf8_lossy(&output.stdout)
        .trim()
        .parse::<usize>()
        .unwrap_or(0);

    Ok(PdfInfo { size, pages })
}

#[derive(serde::Serialize, Clone)]
pub struct ProgressEvent {
    pub stage: String,
    pub current: i32,
    pub total: i32,
    pub message: String,
}

#[derive(serde::Serialize)]
pub struct TranslationResult {
    pub pdf: Option<String>,
    pub markdown: Option<String>,
}

#[tauri::command]
pub async fn translate_pdf(
    state: tauri::State<'_, TranslationState>,
    on_progress: Channel<ProgressEvent>,
    file_path: String,
    config_json: String,
    page_ranges: String,
) -> Result<TranslationResult, String> {
    // Find Python executable (use venv if available)
    let python = find_python();

    // Resolve engine path: check current dir, then parent (for src-tauri dev)
    let engine_path = resolve_project_path("python_engine/main.py");

    if !engine_path.exists() {
        return Err(format!("Engine not found at: {}", engine_path.display()));
    }

    // Project root is the parent directory of python_engine/
    let project_root = engine_path
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| PathBuf::from("."));

    // Build command: use `python -m python_engine.main` so Python can find the package.
    // The output directory is controlled entirely by config.output.default_output_dir
    // (see python_engine/main.py); no need to pass --output-dir from Rust.
    let mut cmd = Command::new(&python);
    cmd.arg("-m")
        .arg("python_engine.main")
        .arg(&file_path)
        .arg("--json-config")
        .arg(&config_json)
        .current_dir(&project_root);

    if !page_ranges.is_empty() {
        cmd.arg("--pages").arg(&page_ranges);
    }

    cmd.stdout(Stdio::piped())
        .stderr(Stdio::piped());

    // Spawn process
    let mut child = cmd.spawn().map_err(|e| format!("Failed to spawn engine: {}", e))?;

    // Record PID so cancel_translation can kill it
    let pid = child.id();
    {
        let mut stored = state.child_id.lock().await;
        *stored = pid;
    }
    state.cancelled.store(false, Ordering::SeqCst);

    let stdout = child.stdout.take().ok_or("Failed to capture stdout")?;
    let reader = BufReader::new(stdout);
    let mut lines = reader.lines();

    let mut result = TranslationResult {
        pdf: None,
        markdown: None,
    };

    // Read progress/events from stdout
    loop {
        // Check if cancelled before every await
        if state.cancelled.load(Ordering::SeqCst) {
            let _ = child.kill().await;
            {
                let mut stored = state.child_id.lock().await;
                *stored = None;
            }
            return Err("Translation cancelled by user".to_string());
        }

        // Read one line with a short timeout so we re-check `cancelled` frequently
        let read_result = timeout(Duration::from_millis(500), lines.next_line()).await;
        let line = match read_result {
            Ok(Ok(l)) => l,
            Ok(Err(e)) => return Err(e.to_string()),
            Err(_) => continue, // timeout → loop back and re-check cancelled
        };

        let line = match line {
            Some(l) => l,
            None => break, // stdout closed (process ended)
        };

        if line.trim().is_empty() {
            continue;
        }

        // Try to parse as JSON event
        if let Ok(event) = serde_json::from_str::<serde_json::Value>(&line) {
            match event.get("type").and_then(|v| v.as_str()) {
                Some("finish") => {
                    if let Some(files) = event.get("output_files") {
                        if let Some(pdf) = files.get("pdf").and_then(|v| v.as_str()) {
                            result.pdf = Some(pdf.to_string());
                        }
                        if let Some(md) = files.get("markdown").and_then(|v| v.as_str()) {
                            result.markdown = Some(md.to_string());
                        }
                    }
                }
                Some("error") => {
                    let msg = event
                        .get("message")
                        .and_then(|v| v.as_str())
                        .unwrap_or("Unknown error");
                    {
                        let mut stored = state.child_id.lock().await;
                        *stored = None;
                    }
                    return Err(msg.to_string());
                }
                Some("progress") => {
                    let progress = ProgressEvent {
                        stage: event.get("stage").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                        current: event.get("current").and_then(|v| v.as_i64()).unwrap_or(0) as i32,
                        total: event.get("total").and_then(|v| v.as_i64()).unwrap_or(100) as i32,
                        message: event.get("message").and_then(|v| v.as_str()).unwrap_or("").to_string(),
                    };
                    let _ = on_progress.send(progress);
                }
                _ => {}
            }
        }
    }

    // Wait for process to finish (also re-check cancellation)
    let status = timeout(Duration::from_secs(5), child.wait()).await;
    let status: std::process::ExitStatus = match status {
        Ok(Ok(s)) => s,
        Ok(Err(e)) => return Err(format!("Process error: {}", e)),
        Err(_) => {
            // child.wait timed out — check if cancelled, otherwise wait more
            if state.cancelled.load(Ordering::SeqCst) {
                let _ = child.kill().await;
                {
                    let mut stored = state.child_id.lock().await;
                    *stored = None;
                }
                return Err("Translation cancelled by user".to_string());
            }
            // Not cancelled, just slow — wait without timeout
            child.wait().await.map_err(|e| e.to_string())?
        }
    };
    {
        let mut stored = state.child_id.lock().await;
        *stored = None;
    }
    if !status.success() {
        // Read stderr for debugging
        let stderr = child.stderr.take();
        let stderr_msg = if let Some(stderr) = stderr {
            let mut reader = BufReader::new(stderr);
            let mut buf = String::new();
            let _ = reader.read_to_string(&mut buf).await;
            if buf.trim().is_empty() {
                format!("Engine exited with code: {:?}", status.code())
            } else {
                format!("Engine exited with code: {:?}\n{}", status.code(), buf.trim())
            }
        } else {
            format!("Engine exited with code: {:?}", status.code())
        };
        return Err(stderr_msg);
    }

    Ok(result)
}

#[tauri::command]
pub async fn open_output_dir(path: Option<String>) -> Result<(), String> {
    // Expand `~` to the user's home directory if present.
    let raw = path.unwrap_or_else(|| "~/Downloads".to_string());
    let expanded: PathBuf = if let Some(rest) = raw.strip_prefix("~") {
        let home = dirs::home_dir().ok_or("Failed to get home dir")?;
        let trimmed = rest.trim_start_matches('/');
        if trimmed.is_empty() { home } else { home.join(trimmed) }
    } else {
        PathBuf::from(raw)
    };

    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg(&expanded)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "windows")]
    {
        Command::new("explorer")
            .arg(&expanded)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(target_os = "linux")]
    {
        Command::new("xdg-open")
            .arg(&expanded)
            .spawn()
            .map_err(|e| e.to_string())?;
    }

    Ok(())
}

#[tauri::command]
pub fn save_config(config_json: String) -> Result<(), String> {
    let config_dir = dirs::config_dir()
        .ok_or("Failed to get config dir")?
        .join("pdf-trans");
    std::fs::create_dir_all(&config_dir).map_err(|e| e.to_string())?;
    let config_path = config_dir.join("config.json");
    std::fs::write(&config_path, config_json).map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn load_config() -> Result<String, String> {
    let config_path = dirs::config_dir()
        .ok_or("Failed to get config dir")?
        .join("pdf-trans")
        .join("config.json");
    if !config_path.exists() {
        return Ok("{}".to_string());
    }
    let content = std::fs::read_to_string(&config_path).map_err(|e| e.to_string())?;
    Ok(content)
}

#[derive(serde::Serialize, serde::Deserialize)]
pub struct TestConnectionResult {
    pub success: bool,
    pub message: String,
}

#[tauri::command]
pub async fn test_api_connection(config_json: String) -> Result<TestConnectionResult, String> {
    let python = find_python();
    let project_root = resolve_project_path("python_engine/main.py")
        .parent()
        .and_then(|p| p.parent())
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| PathBuf::from("."));

    let script = format!(
        r#"import asyncio, json, sys
from python_engine.config import EngineConfig
from python_engine.translator import Translator

async def test():
    config = EngineConfig.from_dict(json.loads(sys.argv[1]))
    t = Translator(config.translation)
    try:
        result = await t._translate_single("Hello, world!", context="Test")
        print(json.dumps({{"success": True, "message": "Connection OK, model responded"}}))
    except Exception as e:
        msg = str(e)
        if len(msg) > 500:
            msg = msg[:500] + "..."
        print(json.dumps({{"success": False, "message": msg}}))

asyncio.run(test())
"#,
    );

    let output = Command::new(&python)
        .arg("-c")
        .arg(&script)
        .arg(&config_json)
        .current_dir(&project_root)
        .output()
        .await
        .map_err(|e| format!("Failed to run test: {}", e))?;

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);

    // Try to parse JSON result from stdout
    for line in stdout.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        if let Ok(result) = serde_json::from_str::<TestConnectionResult>(line) {
            if result.success {
                return Ok(result);
            } else {
                return Ok(result);
            }
        }
    }

    // If no JSON parsed, return stderr or a generic error
    let err_msg = if stderr.trim().is_empty() {
        format!("Test script produced no output. stdout: {}", stdout.trim())
    } else {
        stderr.trim().to_string()
    };
    Ok(TestConnectionResult {
        success: false,
        message: err_msg,
    })
}

#[tauri::command]
pub async fn cancel_translation(state: tauri::State<'_, TranslationState>) -> Result<(), String> {
    let pid = {
        let guard = state.child_id.lock().await;
        *guard
    };

    if let Some(pid) = pid {
        // Kill the process itself
        #[cfg(unix)]
        {
            let _ = Command::new("kill")
                .arg("-9")
                .arg(pid.to_string())
                .output()
                .await;
        }
        #[cfg(not(unix))]
        {
            let _ = Command::new("taskkill")
                .args(["/PID", &pid.to_string(), "/F"])
                .output()
                .await;
        }
        // Signal cancelled — the translate_pdf loop will catch it
        state.cancelled.store(true, Ordering::SeqCst);
        return Ok(());
    }

    Err("No active translation to cancel".to_string())
}

fn find_python() -> String {
    // Check for venv Python first (relative to project root)
    let venv_paths = [
        ".venv/bin/python",
        ".venv/Scripts/python.exe",
        "venv/bin/python",
        "venv/Scripts/python.exe",
    ];

    for path in &venv_paths {
        let resolved = resolve_project_path(path);
        if resolved.exists() {
            return resolved.to_string_lossy().to_string();
        }
    }

    // Fallback to system Python
    "python3".to_string()
}

/// Resolve a project-relative path.
/// When running from `src-tauri/` (dev mode), checks the parent directory.
fn resolve_project_path(rel: &str) -> PathBuf {
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    let in_cwd = cwd.join(rel);
    if in_cwd.exists() {
        return in_cwd;
    }
    // If cwd is src-tauri/, check parent directory
    if let Some(parent) = cwd.parent() {
        let in_parent = parent.join(rel);
        if in_parent.exists() {
            return in_parent;
        }
    }
    in_cwd
}
