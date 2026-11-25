// Supabase connection for the browser.
// Fill in BOTH values from: Supabase dashboard -> Project Settings -> API
//   supabaseUrl : "Project URL"            (looks like https://xxxxxxxx.supabase.co)
//   anonKey     : "anon" / "publishable" key
// The anon (publishable) key is PUBLIC by design: it is safe in the browser because Row Level Security
// protects the data. NEVER put the service_role / secret key here.
window.UMRA_CONFIG = {
  supabaseUrl: 'https://wsewkfhmsbrvvhvylpfg.supabase.co',
  anonKey: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndzZXdrZmhtc2JydnZodnlscGZnIiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTQwODMwNjksImV4cCI6MjEwOTY1OTA2OX0.-_R4ngj6Czowhkx4TbIU6atMDoczXj2n5d64qiRUhoQ'
};
