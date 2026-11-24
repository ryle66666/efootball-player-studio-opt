"""Game modules shared by the compiler and both editor distributions."""
WORLD_HELPERS=('content_pack','content_runtime','player_updates','player_append','player_append_tables','player_skill_bindings','native_iostore',
 'player_movie_overlay','native_movie_pak_source',
 'native_live','archive_data','native_strings','custom_boosters','player_identity_binding','native_models',
 'runtime_document','runtime_resource_reuse','tournament_team_choice','tournament_entry_installation','reply_jobs',
 'tournament_models','tournament_model_v3','tournament_score_grid','tournament_stats',
 'effective_players','native_cpk','tournament_tasks','tournament_preparation','stadium_runtime','tournament_rules',
 'tournament_identity','tournament_squads','tournament_eligibility','tournament_runtime',
 'tournament_sim_strength','tournament_simulation','coach_formula','tournament_promotion',
 'tournament_promotion_ui','tournament_agents','tournament_schedule_ui',
 'native_tournament_schedule_ui','tournament_home','league_availability',
 'tournament_hub','tournament_hub_model','tournament_hub_service','tournament_pitch','tournament_portraits')

PERSONAL_HELPERS=('personal_runtime','personal_binding','division_squads','native_overall',
 'ai_tactical_profiles','ai_division_sessions','ai_match_manager','ai_match_channel',
 'ai_match_runtime','ai_match_state','action_trace','league_rules','art_registry',
 'native_diy_binding','draw_v22')

BUNDLED_GAME_SOURCES=tuple(name+'.py'for name in dict.fromkeys((*WORLD_HELPERS,*PERSONAL_HELPERS)))
