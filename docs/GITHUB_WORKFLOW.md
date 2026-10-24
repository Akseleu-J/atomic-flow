# Мини-протокол работы с GitHub

Репозиторий: [`Akseleu-J/atomic-flow`](https://github.com/Akseleu-J/atomic-flow). Текущая рабочая ветка — `experimental`; изменения публикуются через draft PR №2. Перед командами проверьте `git status --short` и `git branch --show-current`.

## Получение доступа

Сначала используйте уже настроенный зашифрованный vault: `C:\Users\omirb\OneDrive\Документы\ChatGPT\computer_control\shared-access\.env.enc`. В этой рабочей копии скрипт `.local-tools/with-github.ps1` читает из него `GITHUB_TOKEN` и передаёт credential только запущенному `git` или `gh`. Пароль vault вводится интерактивно как `SecureString`; он и сам токен не записываются в этот репозиторий, команды, логи или историю PowerShell. Не выводите содержимое `.env.enc` и не запускайте `gh auth token`.

Если vault недоступен и нужен отдельный токен, на GitHub откройте **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**. Ограничьте его репозиторием `Akseleu-J/atomic-flow`, задайте срок действия и только необходимые права: `Contents: Read and write` для fetch/push и `Pull requests: Read and write` для просмотра и редактирования PR. Для некоторых операций изменения PR через API GitHub может потребоваться `Issues: Read and write`; добавляйте его только если GitHub откажет без него. Не выбирайте все репозитории и лишние права. Скопируйте токен сразу в личное хранилище секретов или vault: GitHub показывает его один раз. Не отправляйте токен в чат и не вставляйте его в URL или командную строку.

Официальные инструкции: [создание и управление токенами](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens), [права fine-grained tokens](https://docs.github.com/en/rest/authentication/permissions-required-for-fine-grained-personal-access-tokens), [аутентификация GitHub CLI через переменную окружения](https://docs.github.com/en/github-cli/github-cli/authenticating-with-github-cli).

## Коммит и push

Выполняйте команды из каталога `atomic-flow/`. Перед коммитом проверьте, что в staging попали только ваши файлы:

```powershell
git status --short
git branch --show-current
git config user.name "Omirbay Akseleu"
git config user.email "omirbajakseleu5@gmail.com"
git add <нужные-файлы>
git diff --cached --check
git diff --cached --stat
git commit -m "Краткое описание изменения"
```

Не используйте `git add .`, если в рабочей копии есть чужие или несвязанные изменения. Автор коммита задаётся Git config и не определяется токеном.

Чтобы отправить текущую ветку с помощью vault, запросите пароль безопасным PowerShell prompt и передайте его скрипту:

```powershell
$vaultPassword = Read-Host "Пароль vault" -AsSecureString
try {
    & .local-tools/with-github.ps1 -Password $vaultPassword -GitArguments @('push', 'origin', 'experimental')
} finally {
    $vaultPassword.Dispose()
}
```

Для первой публикации ветки используйте аргументы `@('push', '--set-upstream', 'origin', 'experimental')`. При истёкшем или отозванном токене остановитесь и обновите vault через владельца; не перебирайте другие аккаунты или токены.

## Pull request и завершение

Перед обновлением PR проверьте diff и состояние CI. Для просмотра PR используйте `gh pr view 2 --repo Akseleu-J/atomic-flow`. Для редактирования title/body передайте те же аргументы в helper, например:

```powershell
$vaultPassword = Read-Host "Пароль vault" -AsSecureString
try {
    & .local-tools/with-github.ps1 -Password $vaultPassword -GhArguments @(
        'pr', 'edit', '2', '--repo', 'Akseleu-J/atomic-flow',
        '--title', '<новый заголовок>', '--body-file', '<путь-к-файлу-с-текстом-PR>'
    )
} finally {
    $vaultPassword.Dispose()
}
```

После push проверьте `git status --short`, `git rev-list --left-right --count HEAD...origin/experimental`, ссылку PR и результат CI. PR оставляйте черновиком, пока владелец отдельно не решит его отправить на review или merge.

Если токен когда-либо попал в файл, лог, ссылку или чат, отзовите его в GitHub и создайте замену. `.env.enc` и расшифрованные секреты не добавляйте в Git.
