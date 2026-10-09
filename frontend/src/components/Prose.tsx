export function Prose({
  title,
  updated,
  children,
}: {
  title: string;
  updated: string;
  children: React.ReactNode;
}) {
  return (
    <article className="mx-auto max-w-2xl space-y-4 text-sm leading-7 [&_h2]:mt-6 [&_h2]:text-base [&_h2]:font-semibold [&_li]:ml-5 [&_li]:list-disc">
      <header>
        <h1 className="text-xl font-bold sm:text-2xl">{title}</h1>
        <p className="mt-1 text-muted">시행일 {updated}</p>
      </header>
      {children}
    </article>
  );
}
