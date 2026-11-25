-- migrate:up

-- The repositories the person who connected the GitHub App may push to. An installation
-- usually covers more repositories than that person can write, and the App's own token
-- can write all of them: the connection is limited to this list.
alter table organization_settings add column github_allowed_repos text[];

-- migrate:down

alter table organization_settings drop column if exists github_allowed_repos;
