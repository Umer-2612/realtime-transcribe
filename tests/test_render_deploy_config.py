from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RenderDeployConfigTest(unittest.TestCase):
    def test_render_blueprint_declares_docker_web_service(self):
        render_yaml = ROOT / "render.yaml"

        self.assertTrue(
            render_yaml.exists(),
            "render.yaml is required for Render blueprint deploys",
        )

        text = render_yaml.read_text()
        required_snippets = [
            "type: web",
            "runtime: docker",
            "dockerfilePath: ./Dockerfile",
            "dockerContext: .",
            "healthCheckPath: /health",
            "PORT",
            "MODEL_ID",
            "LANGUAGE",
            "SESSION_TIMEOUT",
        ]

        for snippet in required_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, text)

    def test_dockerfile_preloads_default_model_at_build_time(self):
        dockerfile = ROOT / "Dockerfile"

        self.assertTrue(dockerfile.exists(), "Dockerfile is required for Render")

        text = dockerfile.read_text()
        required_snippets = [
            "ARG MODEL_ID=iic/SenseVoiceSmall",
            "AutoModel(model=model_id",
            "disable_update=True",
            "MODEL_ID=$MODEL_ID",
        ]

        for snippet in required_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, text)

    def test_github_actions_keepalive_pings_render_health_endpoint(self):
        workflow = ROOT / ".github" / "workflows" / "render-keepalive.yml"

        self.assertTrue(workflow.exists(), "GitHub Actions keepalive workflow is required")

        text = workflow.read_text()
        required_snippets = [
            "cron: \"*/10 * * * *\"",
            "workflow_dispatch:",
            "RENDER_SERVICE_URL",
            "${RENDER_SERVICE_URL%/}/health",
            "curl -fsS",
        ]

        for snippet in required_snippets:
            with self.subTest(snippet=snippet):
                self.assertIn(snippet, text)


if __name__ == "__main__":
    unittest.main()
