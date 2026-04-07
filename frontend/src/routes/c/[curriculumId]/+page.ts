import { api } from '$lib/api';
import { error } from '@sveltejs/kit';
import type { PageLoad } from './$types';

export const ssr = false;

export const load: PageLoad = async ({ params }) => {
	const curriculum = await api.getCurriculum(params.curriculumId).catch(() => null);
	if (!curriculum) {
		error(404, 'Curriculum not found');
	}
	return { curriculum };
};
