const SKILL_LABELS: Record<string, string> = {
  api: "API",
  apis: "APIs",
  aws: "AWS",
  ci_cd: "CI/CD",
  "ci/cd": "CI/CD",
  css: "CSS",
  docker: "Docker",
  django: "Django",
  fastapi: "FastAPI",
  graphql: "GraphQL",
  html: "HTML",
  javascript: "JavaScript",
  kubernetes: "Kubernetes",
  mongodb: "MongoDB",
  mysql: "MySQL",
  "node.js": "Node.js",
  nodejs: "Node.js",
  postgresql: "PostgreSQL",
  python: "Python",
  redis: "Redis",
  rest_api: "REST API",
  "rest api": "REST API",
  terraform: "Terraform",
  typescript: "TypeScript",
};

/** Convert canonical matcher skill keys into labels users can scan easily. */
export function formatSkillLabel(skill: string): string {
  const normalized = skill.trim().toLowerCase();
  const knownLabel = SKILL_LABELS[normalized];
  if (knownLabel) return knownLabel;

  return normalized
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}
