import React from "react";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { Routes } from "./routes";
import "./index.css";

export const App: React.FC = () => {
  const router = createBrowserRouter(Routes);
  return <RouterProvider router={router} />;
};
