export default function ComingSoon({ title, message = 'Em breve' }: { title: string; message?: string }) {
  return (
    <section>
      <h1 className="mb-4 text-xl font-bold">{title}</h1>
      <div className="card text-muted">{message}</div>
    </section>
  )
}
