// Supabase connection for the browser.
// Fill in BOTH values from: Supabase dashboard -> Project Settings -> API
//   supabaseUrl : "Project URL"            (looks like https://xxxxxxxx.supabase.co)
//   anonKey     : "anon" / "publishable" key
// The anon (publishable) key is PUBLIC by design: it is safe in the browser because Row Level Security
// protects the data. NEVER put the service_role / secret key here.
window.UMRA_CONFIG = {
  supabaseUrl: '',
  anonKey: ''
};
