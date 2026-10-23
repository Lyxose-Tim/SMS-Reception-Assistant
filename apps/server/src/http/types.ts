import type { BusinessRow } from '../repos/business';
import type { ConversationRow } from '../repos/conversations';
import type { AuthContext } from '../repos/users';

export interface AppEnv {
  Variables: {
    auth: AuthContext;
    business: BusinessRow;
    conversation: ConversationRow;
  };
}
