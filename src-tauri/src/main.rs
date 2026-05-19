mod commands;

use commands::{cancel_translation, get_pdf_info, load_config, open_output_dir, save_config, test_api_connection, translate_pdf, TranslationState};

fn main() {
    tauri::Builder::default()
        .manage(TranslationState::default())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![translate_pdf, open_output_dir, save_config, load_config, get_pdf_info, test_api_connection, cancel_translation])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
