import ArticleEditor from "../../../../../components/ArticleEditor";
export default async function EditArticle({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params; return <ArticleEditor id={id} />;
}
