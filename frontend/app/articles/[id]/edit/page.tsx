"use client";
import { useParams } from "next/navigation";
import ArticleEditor from "../../../../components/ArticleEditor";
export default function EditArticle() { const { id } = useParams<{ id: string }>(); return <ArticleEditor id={id} />; }
