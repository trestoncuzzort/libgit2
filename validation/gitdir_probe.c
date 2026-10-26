#include <stdio.h>
#include <string.h>
#include <git2.h>

int main(int argc, char **argv)
{
	git_repository *repo = NULL;
	git_config *cfg = NULL;
	git_buf value = GIT_BUF_INIT;
	const git_error *error;
	int result;

	if (argc != 4)
		return 2;
	if (git_libgit2_init() < 0)
		return 2;
	if (!strcmp(argv[1], "bare"))
		result = git_repository_open_bare(&repo, argv[2]);
	else if (!strcmp(argv[1], "discover"))
		result = git_repository_open_ext(&repo, argv[2], 0, NULL);
	else
		result = git_repository_open(&repo, argv[2]);
	if (result == 0 && !strcmp(argv[3], "--public-path")) {
		printf("%s\n", git_repository_path(repo));
		goto done;
	}
	if (result == 0)
		result = git_repository_config(&cfg, repo);
	if (result == 0)
		result = git_config_get_string_buf(&value, cfg, argv[3]);
	if (result == 0)
		printf("%s\n", value.ptr);
done:
	if (result != 0 && result != GIT_ENOTFOUND) {
		error = git_error_last();
		fprintf(stderr, "%d: %s\n", result,
			error ? error->message : "no diagnostic");
	}
	git_buf_dispose(&value);
	git_config_free(cfg);
	git_repository_free(repo);
	git_libgit2_shutdown();
	return result == 0 ? 0 : result == GIT_ENOTFOUND ? 1 : 2;
}
