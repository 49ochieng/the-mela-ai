export default function NotFound() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center">
      <h1 className="text-4xl font-bold mb-2">404</h1>
      <p className="text-muted-foreground">Page not found</p>
      <a href="/" className="mt-4 text-primary hover:underline text-sm">
        Go home
      </a>
    </div>
  );
}
