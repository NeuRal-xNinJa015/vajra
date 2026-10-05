use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;

use tauri::{Manager, RunEvent};

const DEFAULT_BACKEND_PORT: u16 = 8756;

struct Backend {
  url: String,
  child: Mutex<Option<Child>>,
}

#[tauri::command]
fn backend_url(backend: tauri::State<Backend>) -> String {
  backend.url.clone()
}

// V1 runs from the project folder; VAJRA_ROOT overrides it.
fn project_root() -> PathBuf {
  std::env::var("VAJRA_ROOT")
    .map(PathBuf::from)
    .unwrap_or_else(|_| Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join(".."))
}

fn spawn_backend(port: u16) -> std::io::Result<Child> {
  let root = project_root();
  let python = if cfg!(windows) {
    root.join(".venv").join("Scripts").join("python.exe")
  } else {
    root.join(".venv").join("bin").join("python")
  };
  let mut command = Command::new(python);
  command
    .args(["-m", "vajra"])
    .current_dir(root.join("backend"))
    .env("VAJRA_PORT", port.to_string())
    // The backend watches this pid and exits when the app goes away.
    .env("VAJRA_PARENT_PID", std::process::id().to_string());
  #[cfg(windows)]
  {
    use std::os::windows::process::CommandExt;
    command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
  }
  command.spawn()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .plugin(tauri_plugin_fs::init())
    .plugin(tauri_plugin_dialog::init())
    .plugin(tauri_plugin_notification::init())
    .plugin(tauri_plugin_shell::init())
    .invoke_handler(tauri::generate_handler![backend_url])
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(
          tauri_plugin_log::Builder::default()
            .level(log::LevelFilter::Info)
            .build(),
        )?;
      }
      let port = std::env::var("VAJRA_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(DEFAULT_BACKEND_PORT);
      // If the backend cannot start, the app still opens and shows it as disconnected.
      let child = match spawn_backend(port) {
        Ok(child) => Some(child),
        Err(err) => {
          log::error!("could not start the VAJRA backend: {err}");
          None
        }
      };
      app.manage(Backend {
        url: format!("http://127.0.0.1:{port}"),
        child: Mutex::new(child),
      });
      Ok(())
    })
    .build(tauri::generate_context!())
    .expect("error while building tauri application")
    .run(|app, event| {
      if let RunEvent::Exit = event {
        if let Some(mut child) = app.state::<Backend>().child.lock().unwrap().take() {
          let _ = child.kill();
        }
      }
    });
}
